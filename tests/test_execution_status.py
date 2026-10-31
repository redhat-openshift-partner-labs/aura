"""`execution status` and `execution confirm` (Phase 3 task 4.2)."""

from __future__ import annotations

import io
import json
import sqlite3
from contextlib import redirect_stderr, redirect_stdout
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid5

import pytest

from aura.cli import HubAccess, main
from aura.collectors.hub_policy import HubPolicy
from aura.collectors.oc_cli import OpenShiftCLIError
from aura.config import SQLiteStore
from aura.contracts import AuditEventType, CommandState
from aura.execution.derived import execution_state
from aura.execution.records import ExecutionPhase, Resolution
from aura.execution.store import ExecutionStore
from aura.execution.tekton import EXECUTION_ANNOTATION, run_name
from aura.signing import APPROVE, EXECUTE
from store_invariants import ARTIFACT_EVENTS, assert_store_command_invariants, chain, store_write_faults
from test_execution_store import signed_execution
from test_signing import key, listed

PROPOSAL_ID = UUID("12345678-1234-5678-1234-567812345678")
EXECUTION_ID = UUID(int=50)
STATUS_ID = UUID(int=910)
EXECUTOR = "executor@example.com"
APPROVER = "approver@example.com"


@pytest.fixture
def status_world(tmp_path):
    """A Tekton execution dispatched with no result: awaiting_operator."""

    return dispatched_world(tmp_path)


def dispatched_world(tmp_path, **dispatch):
    keys = {name: key(tmp_path, name) for name in ("executor", "approver")}
    signers = listed(EXECUTOR, keys["executor"], EXECUTE) + listed(APPROVER, keys["approver"], APPROVE)
    database = tmp_path / "aura.db"
    store = open_store(database)
    record, event = signed_execution(
        keys["executor"],
        record_id=str(uuid5(EXECUTION_ID, "dispatching")),
        execution_id=str(EXECUTION_ID),
        proposal_id=str(PROPOSAL_ID),
        proposal_type="post_provision_rerun",
        adapter="tekton",
        **dispatch,
    )
    store.insert("audit_events", event)
    store.insert("executions", record)
    world = {"tmp": tmp_path, "database": database, "keys": keys, "runs": [], "retention": None}
    world["hub"] = lambda: HubPolicy(
        allowed_signers=signers, deployment_id=UUID(int=1), pipeline_run_retention=world["retention"]
    )
    return world


def open_store(database: Path, login: str = EXECUTOR) -> ExecutionStore:
    return ExecutionStore.open_sqlite(SQLiteStore(backend="sqlite", path=database, login=login))


def reader(world) -> ExecutionStore:
    return open_store(world["database"]).with_signers(world["hub"]().allowed_signers)


def config(world, principal: str = EXECUTOR, key_name: str = "executor") -> Path:
    path = world["tmp"] / f"{key_name}.toml"
    path.write_text(
        f'[store]\nbackend = "sqlite"\npath = "{world["database"]}"\nlogin = "{principal}"\n\n'
        "[execution]\nadapter_timeout_seconds = 60\n\n"
        f'[signing]\nprincipal = "{principal}"\nkey = "{world["keys"][key_name]}"\nagent = false\n',
        encoding="utf-8",
    )
    return path


def run(world, monkeypatch, *arguments: str, command_id: UUID = STATUS_ID, config_path: Path | None = None):
    monkeypatch.setenv("AURA_CONFIG", str(config_path or config(world)))

    def runs(path):
        if isinstance(world["runs"], Exception):
            raise world["runs"]
        return world["runs"]

    stdout, stderr = io.StringIO(), io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(
            list(arguments),
            command_id_factory=lambda: command_id,
            clock=lambda: datetime.now(UTC),
            hub=HubAccess(policy=lambda path: world["hub"](), runs=runs),
        )
    return code, stdout.getvalue(), stderr.getvalue()


def status(world, monkeypatch, *flags: str, key: str = "status-1", command_id: UUID = STATUS_ID):
    return run(
        world, monkeypatch, "execution", "status", "--proposal-id", str(PROPOSAL_ID),
        "--openshift-kubeconfig", "/kube/aura-readonly", "--idempotency-key", key, *flags,
        command_id=command_id,
    )


def pipeline_run(status: str | None = None, *, owner: UUID = EXECUTION_ID, reason: str = "Succeeded") -> dict:
    run = {
        "metadata": {
            "name": run_name(PROPOSAL_ID), "uid": "uid-1", "namespace": "openshift-pipelines",
            "annotations": {EXECUTION_ANNOTATION: str(owner)},
        },
        "status": {"startTime": "2026-10-30T12:01:00Z"},
    }
    if status is not None:
        run["status"]["conditions"] = [{"type": "Succeeded", "status": status, "reason": reason}]
        if status != "Unknown":
            run["status"]["completionTime"] = "2026-10-30T12:30:00Z"
    return run


def phases(world) -> list[str]:
    return sorted(r.phase.value for r in reader(world).select("executions"))


def backdate(world, minutes: int) -> None:
    connection = sqlite3.connect(world["database"], isolation_level=None)
    connection.execute(
        f"UPDATE executions SET stored_at = datetime('now', '-{minutes} minutes') "
        "WHERE phase = 'dispatching'"
    )
    connection.close()


def test_without_record_it_reports_and_stores_no_observation(world_running) -> None:
    world, monkeypatch = world_running

    code, stdout, stderr = status(world, monkeypatch, "--operator", "viewer@example.com")

    assert code == 0, stderr
    report = json.loads(stdout)
    assert report["state"] == "awaiting_operator"
    assert report["would_record"] == ["submitted"]
    assert report["recorded"] == []
    assert phases(world) == ["dispatching"]
    assert reader(world).select("pipeline_run_observations") == []
    assert [e.event_type for e in chain(reader(world), STATUS_ID)] == [AuditEventType.COMMAND_RECEIVED]


@pytest.fixture
def world_running(status_world, monkeypatch):
    """The run exists and has not started: no Succeeded condition yet. The
    dispatch is past its timeout margin, 60 seconds plus five minutes."""

    status_world["runs"] = [pipeline_run()]
    backdate(status_world, 7)
    return status_world, monkeypatch


def test_a_found_run_resolves_awaiting_operator_to_submitted(world_running) -> None:
    world, monkeypatch = world_running

    code, stdout, stderr = status(world, monkeypatch, "--record")

    assert code == 0, stderr
    assert json.loads(stdout)["recorded"] == ["submitted"]
    store = reader(world)
    assert phases(world) == ["dispatching", "submitted"]
    (submitted,) = [r for r in store.select("executions") if r.phase is ExecutionPhase.SUBMITTED]
    assert submitted.external_result == {
        "pipeline_run": run_name(PROPOSAL_ID), "uid": "uid-1", "namespace": "openshift-pipelines",
    }
    assert submitted.principal == EXECUTOR
    (observation,) = store.select("pipeline_run_observations")
    assert observation.succeeded_status == "Unknown"
    assert [e.event_type for e in chain(store, STATUS_ID)] == [
        AuditEventType.COMMAND_RECEIVED,
        AuditEventType.EXECUTION_OBSERVED,
        AuditEventType.EXECUTION_OBSERVED,
    ]
    assert assert_store_command_invariants(
        store, world["database"], key="status-1", command_id=STATUS_ID
    ) is CommandState.SUCCEEDED


@pytest.mark.parametrize(
    ("condition", "expected"),
    [
        ({"status": "True", "reason": "Succeeded"}, ["dispatching", "submitted", "succeeded"]),
        ({"status": "False", "reason": "PipelineRunTimeout"}, ["dispatching", "failed", "submitted"]),
    ],
    ids=["succeeded", "failed"],
)
def test_a_finished_run_records_its_result(status_world, monkeypatch, condition, expected) -> None:
    status_world["runs"] = [pipeline_run(condition["status"], reason=condition["reason"])]
    backdate(status_world, 7)

    code, _, stderr = status(status_world, monkeypatch, "--record")

    assert code == 0, stderr
    assert phases(status_world) == expected
    if condition["status"] == "False":
        (failed,) = [r for r in reader(status_world).select("executions") if r.phase is ExecutionPhase.FAILED]
        assert failed.failure_reason == "the PipelineRun failed: PipelineRunTimeout"


def test_a_result_is_recorded_only_when_the_phase_rises(world_running) -> None:
    world, monkeypatch = world_running
    assert status(world, monkeypatch, "--record")[0] == 0

    world["runs"] = [pipeline_run("Unknown", reason="Running")]
    code, stdout, _ = status(world, monkeypatch, "--record", key="status-2", command_id=UUID(int=911))
    assert code == 0
    assert json.loads(stdout)["recorded"] == ["running"]

    code, stdout, _ = status(world, monkeypatch, "--record", key="status-3", command_id=UUID(int=912))
    assert code == 0
    assert json.loads(stdout)["recorded"] == []
    world["runs"] = [pipeline_run("True")]
    code, stdout, _ = status(world, monkeypatch, "--record", key="status-4", command_id=UUID(int=913))
    assert json.loads(stdout)["recorded"] == ["succeeded"]
    assert len(reader(world).select("pipeline_run_observations")) == 4
    assert execution_state(reader(world).select("executions")).value == "succeeded"


def test_a_run_of_another_execution_keeps_the_execution_open(status_world, monkeypatch) -> None:
    """Operator decision: the run may be going, so G11 keeps the cluster closed."""

    status_world["runs"] = [pipeline_run("Unknown", owner=UUID(int=999))]
    backdate(status_world, 7)

    code, stdout, stderr = status(status_world, monkeypatch, "--record")

    assert code == 0, stderr
    report = json.loads(stdout)
    assert report["recorded"] == []
    assert "created by another execution" in report["note"]
    assert phases(status_world) == ["dispatching"]
    assert execution_state(reader(status_world).select("executions")).value == "awaiting_operator"
    assert reader(status_world).select("pipeline_run_observations") == []


def test_a_found_run_is_not_recorded_submitted_before_the_margin(status_world, monkeypatch) -> None:
    """The executor's own call may still be storing its result (V4)."""

    status_world["runs"] = [pipeline_run("Unknown", reason="Running")]

    code, stdout, stderr = status(status_world, monkeypatch, "--record")

    assert code == 0, stderr
    report = json.loads(stdout)
    assert report["recorded"] == []
    assert "once the timeout margin has passed" in report["note"]
    assert phases(status_world) == ["dispatching"]
    assert len(reader(status_world).select("pipeline_run_observations")) == 1


def test_the_margin_follows_the_dispatchers_timeout(tmp_path, monkeypatch) -> None:
    """A dispatch under a 30-minute timeout is not resolved after 7 minutes,
    whatever the reader's configured timeout (V6)."""

    world = dispatched_world(tmp_path, adapter_timeout_seconds=1800)
    world["retention"] = timedelta(days=1)
    backdate(world, 7)

    code, stdout, stderr = status(world, monkeypatch, "--record")

    assert code == 0, stderr
    assert json.loads(stdout)["recorded"] == []
    assert "not_dispatched needs 2100" in json.loads(stdout)["note"]


def test_a_fast_clock_cannot_record(status_world, monkeypatch) -> None:
    monkeypatch.setattr(ExecutionStore, "database_time", lambda self: datetime.now(UTC) - timedelta(minutes=6))
    status_world["runs"] = [pipeline_run()]

    code, _, stderr = status(status_world, monkeypatch, "--record")

    assert code == 2
    assert "seconds ahead of the store's" in stderr
    assert phases(status_world) == ["dispatching"]


def test_a_key_not_listed_for_execute_cannot_record(status_world, monkeypatch) -> None:
    monkeypatch.setenv("AURA_CONFIG", str(config(status_world, EXECUTOR, "approver")))

    code, _, stderr = run(
        status_world, monkeypatch, "execution", "status", "--proposal-id", str(PROPOSAL_ID),
        "--openshift-kubeconfig", "/kube/aura-readonly", "--idempotency-key", "s", "--record",
        config_path=config(status_world, EXECUTOR, "approver"),
    )

    assert code == 2
    assert "the signing key is refused for aura-execute" in stderr


def test_a_key_not_listed_for_approve_cannot_confirm(status_world, monkeypatch) -> None:
    code, _, stderr = confirm(status_world, monkeypatch, principal=EXECUTOR, key_name="executor")

    assert code == 2
    assert "the signing key is refused for aura-approve" in stderr
    assert reader(status_world).select("resolution_confirmations") == []


def test_no_run_before_the_margin_records_nothing(status_world, monkeypatch) -> None:
    status_world["retention"] = timedelta(days=1)

    code, stdout, stderr = status(status_world, monkeypatch, "--record")

    assert code == 0, stderr
    report = json.loads(stdout)
    assert report["recorded"] == []
    assert "not_dispatched needs 360" in report["note"]
    assert phases(status_world) == ["dispatching"]


def test_no_run_after_the_margin_inside_retention_is_not_dispatched(status_world, monkeypatch) -> None:
    status_world["retention"] = timedelta(days=1)
    backdate(status_world, 7)

    code, stdout, stderr = status(status_world, monkeypatch, "--record")

    assert code == 0, stderr
    assert json.loads(stdout)["recorded"] == ["not_dispatched"]
    (resolved,) = [r for r in reader(status_world).select("executions") if r.phase is ExecutionPhase.NOT_DISPATCHED]
    assert resolved.resolution is Resolution.TIMEOUT_MARGIN


@pytest.mark.parametrize("retention", [None, timedelta(minutes=5)], ids=["by-count", "expired"])
def test_no_run_without_a_usable_retention_needs_a_manual_resolution(status_world, monkeypatch, retention) -> None:
    status_world["retention"] = retention
    backdate(status_world, 7)

    code, stdout, stderr = status(status_world, monkeypatch, "--record")

    assert code == 0, stderr
    report = json.loads(stdout)
    assert report["recorded"] == []
    assert "execution confirm" in report["note"]
    assert phases(status_world) == ["dispatching"]


def confirm(world, monkeypatch, statement: str = "Both operators checked the hub: no run exists.",
            principal: str = APPROVER, key_name: str = "approver", key: str = "confirm-1"):
    return run(
        world, monkeypatch, "execution", "confirm", "--proposal-id", str(PROPOSAL_ID),
        "--openshift-kubeconfig", "/kube/aura-readonly", "--idempotency-key", key,
        "--statement", statement, command_id=UUID(int=920),
        config_path=config(world, principal, key_name),
    )


def test_a_manual_resolution_names_another_principals_confirmation(status_world, monkeypatch) -> None:
    backdate(status_world, 7)
    code, stdout, stderr = confirm(status_world, monkeypatch)
    assert code == 0, stderr
    confirmation_id = json.loads(stdout)["confirmation_id"]

    code, stdout, stderr = status(status_world, monkeypatch, "--record", "--confirmation-id", confirmation_id)

    assert code == 0, stderr
    (resolved,) = [r for r in reader(status_world).select("executions") if r.phase is ExecutionPhase.NOT_DISPATCHED]
    assert resolved.resolution is Resolution.MANUAL
    assert str(resolved.confirmation_id) == confirmation_id
    assert resolved.failure_reason == "Both operators checked the hub: no run exists."
    confirmer = open_store(status_world["database"], login=APPROVER).with_signers(status_world["hub"]().allowed_signers)
    assert assert_store_command_invariants(
        confirmer, status_world["database"], key="confirm-1", command_id=UUID(int=920)
    ) is CommandState.SUCCEEDED


def test_a_confirmation_by_the_resolver_is_refused(status_world, monkeypatch) -> None:
    backdate(status_world, 7)
    status_world["keys"]["executor-approve"] = key(status_world["tmp"], "executor-approve")
    hub = status_world["hub"]
    signers = hub().allowed_signers + listed(EXECUTOR, status_world["keys"]["executor-approve"], APPROVE)
    status_world["hub"] = lambda: HubPolicy(allowed_signers=signers, deployment_id=UUID(int=1), pipeline_run_retention=None)
    code, stdout, stderr = confirm(status_world, monkeypatch, principal=EXECUTOR, key_name="executor-approve")
    assert code == 0, stderr

    code, _, stderr = status(
        status_world, monkeypatch, "--record", "--confirmation-id", json.loads(stdout)["confirmation_id"]
    )

    assert code == 2
    assert "needs another principal's confirmation" in stderr
    assert phases(status_world) == ["dispatching"]


def test_an_unknown_confirmation_is_refused(status_world, monkeypatch) -> None:
    backdate(status_world, 7)

    code, _, stderr = status(status_world, monkeypatch, "--record", "--confirmation-id", str(UUID(int=5)))

    assert code == 2
    assert "is not stored" in stderr


def test_a_pruned_run_keeps_its_last_observation_and_is_never_not_dispatched(world_running) -> None:
    world, monkeypatch = world_running
    world["retention"] = timedelta(days=1)
    assert status(world, monkeypatch, "--record")[0] == 0
    world["runs"] = []
    backdate(world, 7)

    code, stdout, stderr = status(world, monkeypatch, "--record", key="status-2", command_id=UUID(int=911))

    assert code == 0, stderr
    report = json.loads(stdout)
    assert report["recorded"] == []
    assert report["last_observation"]["name"] == run_name(PROPOSAL_ID)
    assert "gone" in report["note"]
    assert "not_dispatched" not in phases(world)


def test_confirm_is_refused_unless_the_execution_awaits_the_operator(world_running) -> None:
    world, monkeypatch = world_running
    assert status(world, monkeypatch, "--record")[0] == 0

    code, _, stderr = confirm(world, monkeypatch)

    assert code == 2
    assert "is submitted, not awaiting_operator" in stderr
    assert reader(world).select("resolution_confirmations") == []


def test_a_proposal_without_an_execution_is_refused(status_world, monkeypatch) -> None:
    code, _, stderr = run(
        status_world, monkeypatch, "execution", "status", "--proposal-id", str(UUID(int=4)),
        "--openshift-kubeconfig", "/kube/aura-readonly", "--idempotency-key", "s", "--record",
    )

    assert code == 2
    assert "has no execution" in stderr


@pytest.mark.parametrize(
    ("flags", "message"),
    [
        (("--confirmation-id", str(UUID(int=5)), "--operator", "x"), "--confirmation-id requires --record"),
        ((), "--operator is required without --record"),
    ],
    ids=["confirmation-without-record", "no-operator"],
)
def test_argument_errors(status_world, monkeypatch, flags, message) -> None:
    code, _, stderr = status(status_world, monkeypatch, *flags)

    assert code == 2
    assert message in stderr


def test_a_blank_statement_is_refused(status_world, monkeypatch) -> None:
    code, _, stderr = confirm(status_world, monkeypatch, statement=" ")

    assert code == 2
    assert "--statement must not be blank" in stderr


def test_an_unauthorized_role_cannot_read_status(status_world, monkeypatch) -> None:
    code, _, stderr = status(status_world, monkeypatch, "--operator", "p@example.com", "--role", "proposer")

    assert code == 2
    assert "cannot read execution status" in stderr


def test_a_hub_read_failure_fails_the_command(status_world, monkeypatch) -> None:
    status_world["runs"] = OpenShiftCLIError("oc command failed for pipelineruns (exit 1): denied")

    code, _, stderr = status(status_world, monkeypatch, "--record")

    assert code == 2
    assert "denied" in stderr
    (record,) = reader(status_world).select("commands")
    assert record.status is CommandState.FAILED


STATUS_WRITES = [
    "audit_events:command_received",
    "audit_events:execution_observed",
    "pipeline_run_observations",
    "audit_events:execution_observed",
    "executions",
    "commands",
]


def test_status_writes_in_order(world_running) -> None:
    world, monkeypatch = world_running

    with store_write_faults(monkeypatch) as writes:
        assert status(world, monkeypatch, "--record")[0] == 0

    assert writes.labels == STATUS_WRITES


@pytest.mark.parametrize("fail_at", range(len(STATUS_WRITES)), ids=[f"{i}-{w}" for i, w in enumerate(STATUS_WRITES)])
def test_each_status_write_failure_fails_closed(world_running, fail_at) -> None:
    world, monkeypatch = world_running

    with store_write_faults(monkeypatch, fail_at):
        code, stdout, _ = status(world, monkeypatch, "--record")

    assert (code, stdout) == (2, "")
    store = reader(world)
    commands = store.select("commands")
    if fail_at == len(STATUS_WRITES) - 1:
        # Only the terminal record failed.
        assert commands == []
        return
    (record,) = commands
    assert record.status is CommandState.FAILED
    assert chain(store, STATUS_ID)[-1].event_type is AuditEventType.COMMAND_FAILED


def test_a_non_tekton_execution_reads_nothing(tmp_path, monkeypatch, status_world) -> None:
    connection = sqlite3.connect(status_world["database"], isolation_level=None)
    connection.execute("DELETE FROM executions")
    connection.close()
    record, event = signed_execution(
        status_world["keys"]["executor"],
        record_id=str(uuid5(EXECUTION_ID, "dispatching")),
        execution_id=str(EXECUTION_ID),
        proposal_id=str(PROPOSAL_ID),
        adapter="recording",
    )
    store = open_store(status_world["database"])
    store.insert("executions", record)

    code, stdout, stderr = status(status_world, monkeypatch, "--record")

    assert code == 0, stderr
    assert "reads no external system for recording" in json.loads(stdout)["note"]


def test_confirmations_are_artifacts_of_their_command() -> None:
    assert ARTIFACT_EVENTS[AuditEventType.RESOLUTION_CONFIRMED] == ("resolution_confirmations", "confirmation_id")


def test_a_confirmation_of_another_execution_is_refused(status_world, monkeypatch) -> None:
    other_proposal, other_execution = UUID(int=4545), UUID(int=4546)
    record, event = signed_execution(
        status_world["keys"]["executor"],
        record_id=str(uuid5(other_execution, "dispatching")),
        execution_id=str(other_execution),
        proposal_id=str(other_proposal),
        proposal_type="post_provision_rerun",
        adapter="tekton",
        cluster_name="other",
        command_id=str(UUID(int=4547)),
    )
    store = open_store(status_world["database"])
    store.insert("audit_events", event)
    store.insert("executions", record)
    backdate(status_world, 7)
    code, stdout, stderr = run(
        status_world, monkeypatch, "execution", "confirm", "--proposal-id", str(other_proposal),
        "--openshift-kubeconfig", "/kube/aura-readonly", "--idempotency-key", "confirm-other",
        "--statement", "No run exists.", command_id=UUID(int=921),
        config_path=config(status_world, APPROVER, "approver"),
    )
    assert code == 0, stderr

    code, _, stderr = status(
        status_world, monkeypatch, "--record", "--confirmation-id", json.loads(stdout)["confirmation_id"]
    )

    assert code == 2
    assert "is for another execution" in stderr
