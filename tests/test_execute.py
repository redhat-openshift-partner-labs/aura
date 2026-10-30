"""`execute` through the guard, with the recording adapter (task 3.5)."""

from __future__ import annotations

import io
import json
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from uuid import UUID

import pytest

from aura.cli import HubAccess, main
from aura.contracts import AuditEventType, CommandState
from aura.execution.records import ExecutionPhase
from store_invariants import assert_store_command_invariants, chain, store_write_faults
from test_cli_propose import cluster_state_collector
from test_guard import NOW, world  # noqa: F401
from test_proposal_submit import open_store, phase2, proposal_id  # noqa: F401


EXECUTE_ID = UUID(int=900)
WRITES = [
    "audit_events:command_received",
    "audit_events:execution_started",
    "executions",
    "audit_events:execution_finished",
    "executions",
    "commands",
]


def executor_config(world, *, adapters: str = '["recording"]') -> None:
    config = world["tmp"] / "executor.toml"
    config.write_text(
        f'[store]\nbackend = "sqlite"\npath = "{world["database"]}"\nlogin = "executor@example.com"\n\n'
        f"[execution]\nadapters = {adapters}\n\n"
        f'[signing]\nprincipal = "executor@example.com"\nkey = "{world["keys"]["executor"]}"\n'
        "agent = false\n",
        encoding="utf-8",
    )
    return config


def execute(world, monkeypatch, *flags: str, key: str = "execute-1", command_id: UUID = EXECUTE_ID):
    monkeypatch.setenv("AURA_CONFIG", str(executor_config(world)))
    root = world["root"]
    stdout, stderr = io.StringIO(), io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(
            [
                "execute",
                "--proposal-id", proposal_id(root),
                "--openshift-kubeconfig", "/kube/aura-readonly",
                "--fleet-clusters", str(root / "fleet-clusters"),
                "--idempotency-key", key,
                *flags,
            ],
            collector=cluster_state_collector(root / "fleet-clusters"),
            command_id_factory=lambda: command_id,
            clock=lambda: NOW,
            hub=HubAccess(policy=lambda path: world["hub"], effect=lambda path, pid: False),
        )
    return code, stdout.getvalue(), stderr.getvalue()


def reader(world, login: str = "executor@example.com"):
    return open_store(world["database"], login=login).with_signers(world["hub"].allowed_signers)


def test_a_dry_run_runs_the_guard_and_dispatches_nothing(world, monkeypatch) -> None:
    code, stdout, stderr = execute(world, monkeypatch, "--dry-run")

    assert code == 0, stderr
    request = json.loads(stdout)
    assert request["adapter"] == "recording"
    assert request["proposal_id"] == proposal_id(world["root"])
    store = reader(world)
    assert store.select("executions") == []
    assert [e.event_type for e in chain(store, EXECUTE_ID)] == [AuditEventType.COMMAND_RECEIVED]


def test_an_approved_proposal_is_dispatched_once(world, monkeypatch) -> None:
    with store_write_faults(monkeypatch) as writes:
        code, stdout, stderr = execute(world, monkeypatch)

    assert code == 0, stderr
    assert writes.labels == WRITES
    store = reader(world)
    phases = sorted(record.phase.value for record in store.select("executions"))
    assert phases == ["dispatching", "submitted"]
    assert json.loads(stdout)["phase"] == "submitted"
    assert assert_store_command_invariants(
        store, world["database"], key="execute-1", command_id=EXECUTE_ID
    ) is CommandState.SUCCEEDED


def test_a_dispatched_proposal_is_refused_again(world, monkeypatch) -> None:
    assert execute(world, monkeypatch)[0] == 0

    code, _, stderr = execute(world, monkeypatch, key="execute-2", command_id=UUID(int=901))

    assert code == 2
    assert "G6: the proposal is submitted, not approved" in stderr


def test_the_dispatch_rechecks_under_the_lock(world, monkeypatch) -> None:
    """A dispatch that commits after the guard ran is caught by the
    transaction's recheck, so the proposal is dispatched once."""

    import aura.execution.guard as guard

    assert execute(world, monkeypatch)[0] == 0
    # The guard's own G6 and G11 would refuse first; the transaction's
    # recheck keeps the rules it imported.
    monkeypatch.setattr(guard, "g6_one_execution", lambda *args: None)
    monkeypatch.setattr(guard, "g11_cluster_and_rate", lambda *args: None)

    code, _, stderr = execute(world, monkeypatch, key="execute-2", command_id=UUID(int=901))

    assert code == 2
    assert "G6" in stderr
    dispatches = [r for r in reader(world).select("executions") if r.phase is ExecutionPhase.DISPATCHING]
    assert len(dispatches) == 1


def test_an_adapter_without_a_result_leaves_the_execution_awaiting_the_operator(world, monkeypatch) -> None:
    from aura.execution.recording import RecordingAdapter

    def lost(self, request):
        raise TimeoutError("no answer")

    monkeypatch.setattr(RecordingAdapter, "write", lost)

    code, stdout, stderr = execute(world, monkeypatch)

    assert (code, stdout) == (2, "")
    assert "awaiting_operator: run `aura execution status`" in stderr
    store = reader(world)
    assert [r.phase for r in store.select("executions")] == [ExecutionPhase.DISPATCHING]
    assert assert_store_command_invariants(
        store, world["database"], key="execute-1", command_id=EXECUTE_ID
    ) is CommandState.FAILED


def test_an_unapproved_execution_is_refused_before_any_dispatch(world, monkeypatch) -> None:
    import sqlite3

    connection = sqlite3.connect(world["database"], isolation_level=None)
    connection.execute("DELETE FROM approvals")
    connection.close()

    code, _, stderr = execute(world, monkeypatch)

    assert code == 2
    assert "G5" in stderr
    assert reader(world).select("executions") == []


@pytest.mark.parametrize("fail_at", range(len(WRITES)), ids=[f"{i}-{w}" for i, w in enumerate(WRITES)])
def test_each_store_write_failure_fails_closed(world, monkeypatch, fail_at) -> None:
    with store_write_faults(monkeypatch, fail_at) as writes:
        code, stdout, stderr = execute(world, monkeypatch)

    assert (code, stdout) == (2, "")
    store = reader(world)
    dispatched = fail_at > 2
    assert "awaiting_operator" in stderr if dispatched else "awaiting_operator" not in stderr
    dispatches = [r for r in store.select("executions") if r.phase is ExecutionPhase.DISPATCHING]
    assert len(dispatches) == (1 if dispatched else 0)
    assert [r for r in store.select("executions") if r.phase is not ExecutionPhase.DISPATCHING] == []
    if WRITES[fail_at] in {"executions", "commands"} and fail_at >= 4:
        # The result and the terminal record fail together.
        assert store.select("commands") == []
        return
    (record,) = store.select("commands")
    assert record.status is CommandState.FAILED
