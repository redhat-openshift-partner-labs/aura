"""`execute` through the guard, with the recording adapter (task 3.5)."""

from __future__ import annotations

import io
import json
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from pathlib import Path
from uuid import UUID, uuid5

import pytest

from aura.cli import HubAccess, main
from aura.contracts import AuditEventType, CommandState
from aura.execution.records import ExecutionPhase
from store_invariants import assert_store_command_invariants, chain, store_write_faults
from test_cli_propose import cluster_state_collector
from test_guard import NOW, world  # noqa: F401
from test_proposal_submit import open_store, phase2, proposal_id  # noqa: F401


EXECUTE_ID = UUID(int=900)
# G8's regeneration records, each after its event.
REGENERATION = [
    "audit_events:evidence_collected",
    "evidence_snapshots",
    "audit_events:context_assembled",
    "cwa_snapshots",
    "cwa_traces",
    "audit_events:proposal_created",
    "regenerated_proposals",
]
WRITES = [
    "audit_events:command_received",
    *REGENERATION,
    "audit_events:execution_started",
    "executions",
    "audit_events:execution_finished",
    "executions",
    "commands",
]
DISPATCH = WRITES.index("executions")
RESULT = len(WRITES) - 2
REGENERATION_EVENTS = [
    AuditEventType.EVIDENCE_COLLECTED,
    AuditEventType.CONTEXT_ASSEMBLED,
    AuditEventType.PROPOSAL_CREATED,
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


class PerCommandSnapshots:
    """Derives each snapshot ID from the command, as the production
    collectors do, so two runs store distinct snapshots."""

    def __init__(self, collector) -> None:
        self._collector = collector

    def collect(self, *, command_id: UUID, snapshot_id: UUID | None = None, **options):
        return self._collector.collect(
            command_id=command_id, snapshot_id=uuid5(command_id, "evidence"), **options
        )


def execute(
    world, monkeypatch, *flags: str, key: str = "execute-1", command_id: UUID = EXECUTE_ID,
    clock=lambda: NOW, config: Path | None = None,
):
    monkeypatch.setenv("AURA_CONFIG", str(config or executor_config(world)))
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
            collector=PerCommandSnapshots(cluster_state_collector(root / "fleet-clusters")),
            command_id_factory=lambda: command_id,
            clock=clock,
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
    assert [e.event_type for e in chain(store, EXECUTE_ID)] == [
        AuditEventType.COMMAND_RECEIVED, *REGENERATION_EVENTS,
    ]


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
    dispatched = fail_at > DISPATCH
    assert "awaiting_operator" in stderr if dispatched else "awaiting_operator" not in stderr
    dispatches = [r for r in store.select("executions") if r.phase is ExecutionPhase.DISPATCHING]
    assert len(dispatches) == (1 if dispatched else 0)
    results = [r for r in store.select("executions") if r.phase is not ExecutionPhase.DISPATCHING]
    # The result record is stored as soon as it exists.
    assert len(results) == (1 if fail_at > RESULT else 0)
    if fail_at > RESULT:
        # Only the terminal record failed.
        assert store.select("commands") == []
        return
    (record,) = store.select("commands")
    assert record.status is CommandState.FAILED


def test_a_failed_adapter_call_fails_the_command_and_records_the_failure(world, monkeypatch) -> None:
    from aura.adapters import AdapterResult
    from aura.execution.recording import RecordingAdapter

    def refused_write(self, request):
        return AdapterResult(
            request_id=request.request_id, execution_id=request.execution_id,
            adapter=request.adapter, success=False, error="create refused",
        )

    monkeypatch.setattr(RecordingAdapter, "write", refused_write)

    code, stdout, stderr = execute(world, monkeypatch)

    assert (code, stdout) == (2, "")
    assert "create refused" in stderr
    store = reader(world)
    phases = sorted(r.phase.value for r in store.select("executions"))
    assert phases == ["dispatching", "failed"]
    (record,) = store.select("commands")
    assert record.status is CommandState.FAILED


def test_an_unknown_dispatch_outcome_awaits_the_operator(world, monkeypatch) -> None:
    from aura.execution.store import ExecutionStore, StoreOutcomeUnknown

    def unknown(self, table, record, check):
        raise StoreOutcomeUnknown("executions write outcome unknown")

    monkeypatch.setattr(ExecutionStore, "exclusive", unknown)

    code, _, stderr = execute(world, monkeypatch)

    assert code == 2
    assert "awaiting_operator: run `aura execution status`" in stderr


def test_a_tampered_publication_is_reported_without_a_traceback(world, monkeypatch) -> None:
    import sqlite3

    connection = sqlite3.connect(world["database"], isolation_level=None)
    connection.execute("UPDATE published_proposals SET digest = ?", ("0" * 64,))
    connection.close()

    code, stdout, stderr = execute(world, monkeypatch)

    assert (code, stdout) == (2, "")
    assert stderr.startswith("aura: ")


def test_a_second_proposal_of_the_same_cluster_is_refused_at_dispatch(world, monkeypatch) -> None:
    """The guard's G11 runs first; with it removed, the transaction's
    recheck still refuses a second open execution for the cluster."""

    import aura.execution.guard as guard
    from test_guard import store_dispatch

    store_dispatch(world, proposal=UUID(int=4242))
    monkeypatch.setattr(guard, "g11_cluster_and_rate", lambda *args: None)

    code, _, stderr = execute(world, monkeypatch)

    assert code == 2
    assert "G11" in stderr


def test_the_dispatch_rests_on_stored_regeneration_records(world, monkeypatch) -> None:
    """The stored evidence, CWA snapshot and trace, and regenerated
    proposal show why G8 cleared the dispatch (Gate 7)."""

    from aura.execution.action import action_digest

    assert execute(world, monkeypatch)[0] == 0

    store = reader(world)
    (published,) = store.select("published_proposals")
    (regenerated,) = store.select("regenerated_proposals")
    (trace,) = store.select("cwa_traces")
    (cwa,) = store.select("cwa_snapshots")
    (evidence,) = store.select("evidence_snapshots")
    assert action_digest(regenerated) == action_digest(published.proposal)
    assert regenerated.proposal_id != published.proposal_id
    assert regenerated.cwa_trace_id == trace.trace_id
    assert trace.response_digest == regenerated.proposal_digest
    assert (trace.cwa_snapshot_id, trace.cwa_snapshot_digest) == (cwa.snapshot_id, cwa.snapshot_digest)
    assert (cwa.evidence_snapshot_id, cwa.evidence_snapshot_digest) == (
        evidence.snapshot_id, evidence.snapshot_digest,
    )
    assert {evidence.command_id, cwa.command_id, trace.command_id} == {EXECUTE_ID}
    events = {e.event_type: e for e in chain(store, EXECUTE_ID)}
    assert events[AuditEventType.EVIDENCE_COLLECTED].event_id == trace.audit_event_id
    assert events[AuditEventType.CONTEXT_ASSEMBLED].data["cwa_snapshot_id"] == str(cwa.snapshot_id)
    assert events[AuditEventType.PROPOSAL_CREATED].data["proposal_digest"] == regenerated.proposal_digest


def test_a_regeneration_refusal_stores_its_evidence_and_trace(world, monkeypatch) -> None:
    import aura.cli as cli
    from aura.services.proposal import ProposalRefused

    class Refusing:
        provider_name = "test"
        generator = "refusing"

        def generate(self, snapshot, **options):
            raise ProposalRefused(["the cluster changed"])

    monkeypatch.setattr(cli, "_proposal_generator", lambda args: Refusing())

    code, _, stderr = execute(world, monkeypatch)

    assert code == 2
    assert "G8: regeneration refused: proposal refused: the cluster changed" in stderr
    store = reader(world)
    assert len(store.select("evidence_snapshots")) == 1
    assert len(store.select("cwa_snapshots")) == 1
    (trace,) = store.select("cwa_traces")
    assert trace.response_digest is None
    assert store.select("regenerated_proposals") == []
    assert store.select("executions") == []


def test_an_unknown_adapter_outcome_names_its_cause(world, monkeypatch) -> None:
    from aura.execution.recording import RecordingAdapter
    from aura.execution.tekton import TektonOutcomeUnknown

    def already_exists(self, request):
        raise TektonOutcomeUnknown("oc create failed with an unknown outcome")

    monkeypatch.setattr(RecordingAdapter, "write", already_exists)

    code, _, stderr = execute(world, monkeypatch)

    assert code == 2
    assert "did not report its result: oc create failed with an unknown outcome" in stderr
    assert "awaiting_operator: run `aura execution status`" in stderr
    assert [r.phase for r in reader(world).select("executions")] == [ExecutionPhase.DISPATCHING]


@pytest.mark.parametrize("field", ["request_id", "execution_id"])
def test_a_result_for_another_request_awaits_the_operator(world, monkeypatch, field) -> None:
    from aura.execution.recording import RecordingAdapter

    original = RecordingAdapter.write

    def mismatched(self, request):
        return original(self, request).model_copy(update={field: UUID(int=999)})

    monkeypatch.setattr(RecordingAdapter, "write", mismatched)

    code, _, stderr = execute(world, monkeypatch)

    assert code == 2
    assert "reported a result for another request" in stderr
    assert [r.phase for r in reader(world).select("executions")] == [ExecutionPhase.DISPATCHING]


def test_a_secret_named_result_value_is_redacted(world, monkeypatch) -> None:
    from aura.execution.recording import RecordingAdapter

    original = RecordingAdapter.write

    def leaky(self, request):
        result = original(self, request)
        return result.model_copy(update={"external_result": {**result.external_result, "token": "s3cret"}})

    monkeypatch.setattr(RecordingAdapter, "write", leaky)

    assert execute(world, monkeypatch)[0] == 0

    store = reader(world)
    (submitted,) = [r for r in store.select("executions") if r.phase is ExecutionPhase.SUBMITTED]
    assert "s3cret" not in submitted.model_dump_json()
    finished = [e for e in chain(store, EXECUTE_ID) if e.event_type is AuditEventType.EXECUTION_FINISHED]
    assert "s3cret" not in finished[0].model_dump_json()


def test_the_result_record_is_stamped_after_the_call(world, monkeypatch) -> None:
    from datetime import timedelta

    times = iter(NOW + timedelta(seconds=step) for step in range(1000))

    code, _, stderr = execute(world, monkeypatch, clock=lambda: next(times))

    assert code == 0, stderr
    records = {r.phase: r for r in reader(world).select("executions")}
    assert records[ExecutionPhase.SUBMITTED].created_at > records[ExecutionPhase.DISPATCHING].created_at


def test_the_dispatch_recheck_reads_a_fresh_clock(world, monkeypatch) -> None:
    """An approval that expires while the guard regenerates the proposal
    no longer counts at dispatch."""

    from datetime import timedelta

    import aura.execution.execute as execute_module
    import aura.execution.guard as guard
    from aura.execution.guard import GuardRefusal

    guard_times = []
    original = guard.g5_approvals

    def guard_g5(context, proposal, action):
        guard_times.append(context.now)
        return original(context, proposal, action)

    def recheck_g5(context, proposal, action):
        raise GuardRefusal("G5", f"rechecked at {context.now.isoformat()}")

    monkeypatch.setattr(guard, "g5_approvals", guard_g5)
    monkeypatch.setattr(execute_module, "g5_approvals", recheck_g5)
    times = iter(NOW + timedelta(seconds=step) for step in range(1000))

    code, _, stderr = execute(world, monkeypatch, clock=lambda: next(times))

    assert code == 2
    (guard_time,) = guard_times
    rechecked = datetime.fromisoformat(stderr.split("rechecked at ")[1].split()[0])
    assert rechecked > guard_time
    assert reader(world).select("executions") == []


def test_a_failed_preflight_refuses_the_execution(world, monkeypatch) -> None:
    from aura.execution.recording import RecordingAdapter

    monkeypatch.setattr(RecordingAdapter, "preflight", lambda self: "the system is unreachable")

    code, _, stderr = execute(world, monkeypatch)

    assert code == 2
    assert "G12: preflight failed: the system is unreachable" in stderr
    assert reader(world).select("executions") == []


def test_the_configured_hourly_limit_reaches_the_guard(world, monkeypatch) -> None:
    from test_guard import store_dispatch

    config = executor_config(world)
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            "[execution]\n", "[execution]\nmax_dispatches_per_hour = 1\n"
        ),
        encoding="utf-8",
    )
    # One dispatch, of another cluster's proposal, in the past hour.
    store_dispatch(world, proposal=UUID(int=4343), cluster="other")

    code, _, stderr = execute(world, monkeypatch, config=config)

    assert code == 2
    assert "G11: 1 executions were dispatched in the past hour" in stderr
