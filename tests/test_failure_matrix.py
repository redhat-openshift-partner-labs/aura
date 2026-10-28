from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid5

import pytest

import aura.cli as cli
from aura.collectors.git_inventory import GitInventoryError
from aura.collectors.git_staleness import ProposalRecordUnreadable
from aura.collectors.oc_cli import OpenShiftCLIError
from aura.audit_store import JsonAuditEventStore
from aura.command_store import JsonCommandStore
from aura.contracts import (
    AuditEvent,
    AuditEventType,
    CommandState,
    EvidenceSnapshot,
)
from aura.proposal_store import JsonProposalStore, ProposalNotFound
from aura.state import CommandStateMachine
from failure_injection import WRITE_PHASES, deny_access, run_cli
from invariants import (
    assert_command_invariants,
    command_chain,
    proposals_of,
    record_files,
)
from test_cli_diagnose_state import FakeClusterStateCollector
from aura.collectors.git_inventory import GitInventoryCollector
from test_cli_propose import cluster_state_collector, fleet_clusters
from test_cluster_edit_proposal import ABSENT, fleet
from aura.collectors.pipeline_definitions import PipelineDefinitionError
from test_rerun_proposal import FULL_METADATA, LATEST, run, rerun_collector
from test_git_inventory_collector import commit_repository, write_cluster
from test_git_staleness import deprovision_proposal
from aura.collectors.scaffold_evidence import ScaffoldEvidenceCollector
from aura.scaffold import (
    ScaffoldCheckoutError,
    ScaffoldRequest,
    ScaffoldRunError,
)
from fake_scaffold import fake_scaffold, fleet_with_templates
from test_scaffold_proposal import FakeOpenShiftClient


COMMAND_ID = UUID(int=7)
KEY = "matrix-key"
RECORD_DIRS = (
    "commands",
    "snapshot",
    "audit",
    "cwa",
    "diagnosis",
    "proposal",
)
SOURCE_ERRORS = (GitInventoryError, OpenShiftCLIError)
STORE_ERRORS = (GitInventoryError, ProposalNotFound, ProposalRecordUnreadable)
RERUN_ERRORS = (*SOURCE_ERRORS, PipelineDefinitionError)
SCAFFOLD_ERRORS = (*SOURCE_ERRORS, ScaffoldCheckoutError, ScaffoldRunError)
RECEIVED = "AuditEvent:command_received"
EVIDENCE = ("EvidenceSnapshot", "AuditEvent:evidence_collected")
ASSEMBLED = ("CWASnapshot",)
CONTEXT = ("CWATrace", "AuditEvent:context_assembled")
DIAGNOSED = (
    RECEIVED,
    *EVIDENCE,
    *ASSEMBLED,
    "DiagnosisRecord",
    *CONTEXT,
    "AuditEvent:model_called",
    "CommandEnvelope",
)
PROPOSED = (
    RECEIVED,
    *EVIDENCE,
    *ASSEMBLED,
    *CONTEXT,
    "AuditEvent:proposal_created",
    "Proposal",
    "CommandEnvelope",
)
REFUSED = (
    RECEIVED,
    *EVIDENCE,
    *ASSEMBLED,
    *CONTEXT,
    "AuditEvent:command_refused",
    "CommandEnvelope",
)


@dataclass(frozen=True)
class Scenario:
    name: str
    build: Callable[[Path], tuple[list[str], object | None]]
    writes: tuple[str, ...]
    status: CommandState = CommandState.SUCCEEDED
    read_errors: tuple[type[Exception], ...] = SOURCE_ERRORS

    @property
    def exit_code(self) -> int:
        return 0 if self.status is CommandState.SUCCEEDED else 2

    @property
    def audited(self) -> bool:
        return RECEIVED in self.writes


def operator(root: Path) -> list[str]:
    return [
        "--operator",
        "operator@example.com",
        "--idempotency-key",
        KEY,
        "--command-dir",
        str(root / "commands"),
    ]


def records(root: Path, *names: str) -> list[str]:
    arguments: list[str] = []
    for name in names:
        arguments.extend([f"--{name}-dir", str(root / name)])
    return arguments


def cluster(root: Path) -> list[str]:
    return [
        "--cluster",
        "example",
        "--fleet-clusters",
        str(root / "fleet-clusters"),
        "--openshift-kubeconfig",
        str(root / "kubeconfig"),
    ]


def inventory(*names: str):
    def build(root: Path):
        return (
            [
                "inventory",
                "list",
                "--fleet-clusters",
                str(root),
                *records(root, *names),
                *operator(root),
            ],
            FakeClusterStateCollector(),
        )

    return build


def workflow(*names: str):
    def build(root: Path):
        return (
            [
                "workflow",
                "inspect",
                "--openshift-kubeconfig",
                str(root / "kubeconfig"),
                *records(root, *names),
                *operator(root),
            ],
            FakeClusterStateCollector(),
        )

    return build


def diagnose(complete: bool):
    def build(root: Path):
        return (
            [
                "diagnose",
                "state",
                *cluster(root),
                *records(root, "snapshot", "audit", "cwa", "diagnosis"),
                *operator(root),
            ],
            FakeClusterStateCollector(complete=complete),
        )

    return build


def propose(sentinel: str | None):
    def build(root: Path):
        collector = cluster_state_collector(fleet_clusters(root, sentinel))
        return (
            [
                "propose",
                "deprovision",
                *cluster(root),
                *records(root, "snapshot", "audit", "cwa", "proposal"),
                "--role",
                "proposer",
                *operator(root),
            ],
            collector,
        )

    return build


def cluster_edit(metadata: object = ABSENT, template: str = "crossplane"):
    def build(root: Path):
        fleet(root, metadata, template=template)
        return (
            [
                "propose",
                "cluster-edit",
                *cluster(root),
                *records(root, "snapshot", "audit", "cwa", "proposal"),
                "--role",
                "proposer",
                *operator(root),
            ],
            FakeClusterStateCollector(
                git_collector=GitInventoryCollector(root / "fleet-clusters")
            ),
        )

    return build


def rerun(*runs: dict):
    def build(root: Path):
        fleet(root, FULL_METADATA)
        return (
            [
                "propose",
                "post-provision-rerun",
                *cluster(root),
                *records(root, "snapshot", "audit", "cwa", "proposal"),
                "--role",
                "proposer",
                *operator(root),
            ],
            rerun_collector(root / "fleet-clusters", *runs),
        )

    return build


def scaffold(name: str):
    def build(root: Path):
        fleet = fleet_with_templates(root / "fleet-clusters")
        checkout = fake_scaffold(root / "fleet-cluster-scaffold")
        return (
            [
                "propose",
                "scaffold",
                "--cluster",
                name,
                "--region",
                "us-east-1",
                "--tier",
                "base",
                "--scaffold",
                str(checkout),
                "--fleet-clusters",
                str(fleet),
                "--openshift-kubeconfig",
                str(root / "kubeconfig"),
                *records(root, "snapshot", "audit", "cwa", "proposal"),
                "--role",
                "proposer",
                *operator(root),
            ],
            ScaffoldEvidenceCollector(
                request=ScaffoldRequest(
                    cloud="aws",
                    cluster_name=name,
                    region="us-east-1",
                    tier="base",
                ),
                git_collector=GitInventoryCollector(fleet),
                openshift_client=FakeOpenShiftClient(
                    ("existing",), ("existing",)
                ),
                scaffold_checkout=checkout,
                kustomize_version=lambda: "v5.8.1",
            ),
        )

    return build


def proposal_status(*names: str):
    def build(root: Path):
        fleet = root / "fleet-clusters"
        write_cluster(fleet)
        JsonProposalStore(root / "proposal").save(
            deprovision_proposal(commit_repository(fleet))
        )
        return (
            [
                "proposal",
                "status",
                "--fleet-clusters",
                str(fleet),
                *records(root, "proposal", *names),
                *operator(root),
            ],
            None,
        )

    return build


SCENARIOS = [
    Scenario("inventory-minimal", inventory(), ("CommandEnvelope",)),
    Scenario(
        "inventory-audited",
        inventory("audit"),
        (RECEIVED, "AuditEvent:evidence_collected", "CommandEnvelope"),
    ),
    Scenario(
        "inventory-context",
        inventory("snapshot", "audit", "cwa"),
        (RECEIVED, *EVIDENCE, *ASSEMBLED, *CONTEXT, "CommandEnvelope"),
    ),
    Scenario(
        "inventory-diagnosed",
        inventory("snapshot", "audit", "cwa", "diagnosis"),
        DIAGNOSED,
    ),
    Scenario("workflow-minimal", workflow(), ("CommandEnvelope",)),
    Scenario(
        "workflow",
        workflow("snapshot", "audit"),
        (RECEIVED, *EVIDENCE, "CommandEnvelope"),
    ),
    Scenario("diagnose", diagnose(True), DIAGNOSED),
    Scenario(
        "diagnose-refused",
        diagnose(False),
        REFUSED,
        CommandState.REFUSED,
    ),
    Scenario("propose", propose(None), PROPOSED),
    Scenario(
        "propose-refused",
        propose("example-archive"),
        REFUSED,
        CommandState.REFUSED,
    ),
    Scenario("cluster-edit-file", cluster_edit(), PROPOSED),
    Scenario(
        "cluster-edit-keys",
        cluster_edit("htpasswd-provider-name: Red Hat"),
        PROPOSED,
    ),
    Scenario(
        "cluster-edit-refused",
        cluster_edit(template="crossplane-scoped"),
        REFUSED,
        CommandState.REFUSED,
    ),
    Scenario(
        "rerun",
        rerun(LATEST),
        PROPOSED,
        read_errors=RERUN_ERRORS,
    ),
    Scenario(
        "rerun-refused",
        rerun(
            run(
                "post-provision-example-new",
                "2026-01-02T00:00:00Z",
                succeeded=None,
            )
        ),
        REFUSED,
        CommandState.REFUSED,
        read_errors=RERUN_ERRORS,
    ),
    Scenario(
        "scaffold",
        scaffold("new-cluster"),
        PROPOSED,
        read_errors=SCAFFOLD_ERRORS,
    ),
    Scenario(
        "scaffold-refused",
        scaffold("existing"),
        REFUSED,
        CommandState.REFUSED,
        read_errors=SCAFFOLD_ERRORS,
    ),
    Scenario(
        "proposal-status-minimal",
        proposal_status(),
        ("CommandEnvelope",),
        read_errors=STORE_ERRORS,
    ),
    Scenario(
        "proposal-status",
        proposal_status("snapshot", "audit"),
        (RECEIVED, *EVIDENCE, "CommandEnvelope"),
        read_errors=STORE_ERRORS,
    ),
]
READ_FAILURES = [
    (scenario, error)
    for scenario in SCENARIOS
    for error in scenario.read_errors
]


UNEXPECTED_ERRORS = (RuntimeError, RecursionError, UnicodeDecodeError)
UNEXPECTED_FAILURES = [
    (scenario, error)
    for scenario in SCENARIOS
    for error in UNEXPECTED_ERRORS
]
MODEL_FAILURES = [
    (
        scenario,
        "ClusterStateDiagnosisModel"
        if scenario.name.startswith("diagnose")
        else "_proposal_generator",
    )
    for scenario in SCENARIOS
    if scenario.name.startswith(
        ("diagnose", "propose", "cluster-edit", "rerun", "scaffold")
    )
]


def injected(error: type[BaseException]) -> BaseException:
    if error is UnicodeDecodeError:
        return UnicodeDecodeError(
            "utf-8", b"\xff", 0, 1, "injected read failure"
        )
    return error("injected read failure")


def internal_error(error: BaseException) -> str:
    return f"aura: internal error: {type(error).__name__}: {error}\n"


class FailingReadCollector:
    def __init__(self, error: type[BaseException]) -> None:
        self._error = error

    def collect(self, **_: object) -> EvidenceSnapshot:
        raise injected(self._error)


class FailingModel:
    provider_name = "injected"
    model_name = "injected"
    generator = "injected"

    def diagnose(self, snapshot: object) -> object:
        raise RuntimeError("injected model failure")

    def generate(self, snapshot: object, **_: object) -> object:
        raise RuntimeError("injected model failure")


def stored(root: Path) -> dict[Path, bytes]:
    return record_files(*(root / name for name in RECORD_DIRS))


def check(root: Path, before: dict[Path, bytes], *, expect_record=True):
    return assert_command_invariants(
        command_dir=root / "commands",
        idempotency_key=KEY,
        audit_dir=root / "audit",
        cwa_dir=root / "cwa",
        proposal_dir=root / "proposal",
        expect_record=expect_record,
        before=before,
    )


def execute(
    scenario: Scenario,
    root: Path,
    monkeypatch,
    *,
    collector: object | None = None,
    fail_at: int | None = None,
    phase: str = WRITE_PHASES[0],
):
    arguments, built_collector = scenario.build(root)
    before = stored(root)
    run = run_cli(
        arguments,
        monkeypatch,
        collector=collector or built_collector,
        command_id=COMMAND_ID,
        clock=lambda: datetime(2026, 1, 1, tzinfo=UTC),
        fail_at=fail_at,
        phase=phase,
    )
    return run, before


def scenario_id(item: object) -> str:
    return item.name if isinstance(item, Scenario) else item.__name__


@pytest.mark.parametrize("scenario", SCENARIOS, ids=scenario_id)
def test_clean_run_writes_in_documented_order(
    tmp_path,
    monkeypatch,
    scenario,
) -> None:
    run, before = execute(scenario, tmp_path, monkeypatch)

    assert run.exit_code == scenario.exit_code, run.stderr
    assert [write.label for write in run.writes] == list(scenario.writes)
    assert set(stored(tmp_path)) - set(before) == {
        write.path for write in run.writes
    }, "a record was written without passing through the recorder"
    assert check(tmp_path, before).status is scenario.status


@pytest.mark.parametrize("phase", WRITE_PHASES)
@pytest.mark.parametrize("scenario", SCENARIOS, ids=scenario_id)
def test_each_write_failure_fails_closed(
    tmp_path,
    monkeypatch,
    scenario,
    phase,
) -> None:
    failure_writes = ["CommandEnvelope"]
    if scenario.audited:
        failure_writes.append("AuditEvent:command_failed")
    for index, label in enumerate(scenario.writes):
        case = f"{scenario.name}: write {index} ({label}) fails {phase}"
        root = tmp_path / str(index)
        root.mkdir()

        run, before = execute(
            scenario, root, monkeypatch, fail_at=index, phase=phase
        )

        assert run.exit_code == 2, case
        assert run.stdout == "", case
        assert "injected write failure" in run.stderr, case
        final_write = index == len(scenario.writes) - 1
        record = check(root, before, expect_record=not final_write)
        after = [write.label for write in run.writes[index + 1 :]]
        if final_write:
            assert (record, after) == (None, []), case
            continue
        assert record.status is CommandState.FAILED, case
        assert after == failure_writes, case
        assert proposals_of(root / "proposal", COMMAND_ID) == [], case
        if scenario.audited:
            stored_before = [
                UUID(write.path.stem)
                for write in run.writes[:index]
                if write.label.startswith("AuditEvent:")
            ]
            failed = command_chain(root / "audit", COMMAND_ID)[-1]
            assert failed.event_type is AuditEventType.COMMAND_FAILED, case
            assert failed.previous_event_id == (
                stored_before[-1] if stored_before else None
            ), case


@pytest.mark.parametrize(("scenario", "error"), READ_FAILURES, ids=scenario_id)
def test_evidence_read_failure_fails_closed(
    tmp_path,
    monkeypatch,
    scenario,
    error,
) -> None:
    run, before = execute(
        scenario,
        tmp_path,
        monkeypatch,
        collector=FailingReadCollector(error),
    )

    assert run.exit_code == 2
    assert run.stderr == "aura: injected read failure\n"
    assert check(tmp_path, before).status is CommandState.FAILED
    if scenario.audited:
        assert [
            event.event_type
            for event in command_chain(tmp_path / "audit", COMMAND_ID)
        ] == [AuditEventType.COMMAND_RECEIVED, AuditEventType.COMMAND_FAILED]


def event_types(root: Path) -> list[AuditEventType]:
    return [
        event.event_type
        for event in command_chain(root / "audit", COMMAND_ID)
    ]


def scenario_named(name: str) -> Scenario:
    return next(scenario for scenario in SCENARIOS if scenario.name == name)


def assert_internal_failure(
    run,
    root: Path,
    before: dict[Path, bytes],
    error: BaseException,
) -> None:
    assert run.exit_code == 2
    assert run.stdout == ""
    assert run.stderr == internal_error(error)
    assert check(root, before).status is CommandState.FAILED


@pytest.mark.parametrize(
    ("scenario", "error"), UNEXPECTED_FAILURES, ids=scenario_id
)
def test_unexpected_read_error_fails_closed(
    tmp_path,
    monkeypatch,
    scenario,
    error,
) -> None:
    run, before = execute(
        scenario,
        tmp_path,
        monkeypatch,
        collector=FailingReadCollector(error),
    )

    assert_internal_failure(run, tmp_path, before, injected(error))
    if scenario.audited:
        assert event_types(tmp_path) == [
            AuditEventType.COMMAND_RECEIVED,
            AuditEventType.COMMAND_FAILED,
        ]


class SecretMessageCollector:
    def collect(self, **_: object) -> EvidenceSnapshot:
        raise RuntimeError("token=collector-secret")


@pytest.mark.parametrize(
    "scenario",
    [scenario for scenario in SCENARIOS if scenario.audited],
    ids=scenario_id,
)
def test_unexpected_error_text_is_not_stored(
    tmp_path,
    monkeypatch,
    scenario,
) -> None:
    run, before = execute(
        scenario, tmp_path, monkeypatch, collector=SecretMessageCollector()
    )

    assert_internal_failure(
        run, tmp_path, before, RuntimeError("token=collector-secret")
    )
    failed = command_chain(tmp_path / "audit", COMMAND_ID)[-1]
    assert failed.data["reason"] == "internal error: RuntimeError"
    for path in set(stored(tmp_path)) - set(before):
        assert b"collector-secret" not in path.read_bytes(), path


@pytest.mark.parametrize(
    ("scenario", "target"),
    MODEL_FAILURES,
    ids=lambda item: scenario_id(item) if isinstance(item, Scenario) else item,
)
def test_unexpected_model_error_fails_closed(
    tmp_path,
    monkeypatch,
    scenario,
    target,
) -> None:
    monkeypatch.setattr(cli, target, lambda *_: FailingModel())

    run, before = execute(scenario, tmp_path, monkeypatch)

    assert_internal_failure(
        run, tmp_path, before, RuntimeError("injected model failure")
    )
    assert proposals_of(tmp_path / "proposal", COMMAND_ID) == []
    assert event_types(tmp_path) == [
        AuditEventType.COMMAND_RECEIVED,
        AuditEventType.EVIDENCE_COLLECTED,
        AuditEventType.COMMAND_FAILED,
    ]


def test_unexpected_error_after_terminal_record_writes_nothing(
    tmp_path,
    monkeypatch,
) -> None:
    scenario = scenario_named("propose")
    complete = cli._complete_command

    def complete_then_fail(*arguments: object) -> int:
        complete(*arguments)
        raise RuntimeError("injected model failure")

    monkeypatch.setattr(cli, "_complete_command", complete_then_fail)

    run, before = execute(scenario, tmp_path, monkeypatch)

    assert run.exit_code == 2
    assert run.stderr == internal_error(
        RuntimeError("injected model failure")
    )
    assert [write.label for write in run.writes] == list(scenario.writes)
    assert check(tmp_path, before).status is CommandState.SUCCEEDED
    assert event_types(tmp_path)[-1] is AuditEventType.PROPOSAL_CREATED


@pytest.mark.parametrize(
    "scenario",
    [
        scenario
        for scenario in SCENARIOS
        if scenario.audited and scenario.status is CommandState.REFUSED
    ],
    ids=scenario_id,
)
def test_unexpected_error_after_command_refused_keeps_the_refusal(
    tmp_path,
    monkeypatch,
    scenario,
) -> None:
    store_terminal = cli._store_terminal_command
    raised: list[CommandState] = []

    def refuse_then_fail(args, command):
        # Fail the route's own refused record once; the failure path's
        # write goes through.
        if not raised:
            raised.append(command.status)
            raise RuntimeError("injected terminal failure")
        return store_terminal(args, command)

    monkeypatch.setattr(cli, "_store_terminal_command", refuse_then_fail)

    run, before = execute(scenario, tmp_path, monkeypatch)

    assert run.exit_code == 2
    assert run.stdout == ""
    assert run.stderr == internal_error(
        RuntimeError("injected terminal failure")
    )
    assert raised == [CommandState.REFUSED]
    assert check(tmp_path, before).status is CommandState.REFUSED
    assert event_types(tmp_path)[-1] is AuditEventType.COMMAND_REFUSED


def test_unexpected_error_with_unreadable_terminal_record_writes_nothing(
    tmp_path,
    monkeypatch,
) -> None:
    scenario = scenario_named("propose")
    complete = cli._complete_command

    def complete_then_fail(*arguments: object) -> int:
        complete(*arguments)
        (record,) = (tmp_path / "commands").glob("*.json")
        deny_access(monkeypatch, "read_text", record)
        raise RuntimeError("injected model failure")

    monkeypatch.setattr(cli, "_complete_command", complete_then_fail)

    run, _ = execute(scenario, tmp_path, monkeypatch)

    assert run.exit_code == 2
    assert run.stderr == (
        internal_error(RuntimeError("injected model failure"))
        + f"aura: idempotency key record is unreadable: {KEY}\n"
    )
    assert [write.label for write in run.writes] == list(scenario.writes)


def test_unexpected_error_with_unreadable_audit_stores_failed_record(
    tmp_path,
    monkeypatch,
) -> None:
    scenario = scenario_named("inventory-audited")
    (tmp_path / "audit").mkdir()
    (tmp_path / "audit" / "notes.json").write_text("{}", encoding="utf-8")

    run, _ = execute(
        scenario,
        tmp_path,
        monkeypatch,
        collector=FailingReadCollector(RuntimeError),
    )

    assert run.exit_code == 2
    assert run.stderr == (
        internal_error(injected(RuntimeError))
        + "aura: record name is not an ID: notes.json\n"
    )
    assert [write.label for write in run.writes] == [
        RECEIVED,
        "CommandEnvelope",
    ]
    record = JsonCommandStore(tmp_path / "commands").load(KEY)
    assert record.status is CommandState.FAILED


def write_other_event(root: Path, command_id: UUID, **changes: object) -> None:
    event = AuditEvent(
        event_id=UUID(int=7),
        command_id=command_id,
        event_type=AuditEventType.COMMAND_RECEIVED,
        occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
        actor="other",
        data={},
    )
    document = {**event.model_dump(mode="json"), **changes}
    (root / "audit").mkdir()
    (root / "audit" / f"{event.event_id}.json").write_text(
        json.dumps(document), encoding="utf-8"
    )


@pytest.mark.parametrize(
    "changes",
    [{"event_digest": ""}, {"actor": ""}, {"event_type": "unknown"}],
    ids=["blank-digest", "invalid-field", "unknown-type"],
)
def test_unexpected_error_skips_another_commands_malformed_event(
    tmp_path,
    monkeypatch,
    changes,
) -> None:
    write_other_event(tmp_path, UUID(int=8), **changes)

    run, _ = execute(
        scenario_named("inventory-audited"),
        tmp_path,
        monkeypatch,
        collector=FailingReadCollector(RuntimeError),
    )

    # The invariant helpers load every audit record, including the
    # malformed one, so this command's records are read by ID.
    assert run.exit_code == 2
    assert run.stderr == internal_error(injected(RuntimeError))
    assert [write.label for write in run.writes] == [
        RECEIVED,
        "CommandEnvelope",
        "AuditEvent:command_failed",
    ]
    record = JsonCommandStore(tmp_path / "commands").load(KEY)
    assert record.status is CommandState.FAILED
    failed = JsonAuditEventStore(tmp_path / "audit").load(
        uuid5(COMMAND_ID, AuditEventType.COMMAND_FAILED.value)
    )
    assert failed.previous_event_id == uuid5(COMMAND_ID, "command_received")


@pytest.mark.parametrize(
    "contents",
    ["{", json.dumps({"command_id": str(COMMAND_ID), "event_digest": ""})],
    ids=["unparseable", "names-this-command"],
)
def test_unexpected_error_fails_closed_on_an_unattributable_event(
    tmp_path,
    monkeypatch,
    contents,
) -> None:
    (tmp_path / "audit").mkdir()
    (tmp_path / "audit" / f"{UUID(int=7)}.json").write_text(
        contents, encoding="utf-8"
    )

    run, _ = execute(
        scenario_named("inventory-audited"),
        tmp_path,
        monkeypatch,
        collector=FailingReadCollector(RuntimeError),
    )

    assert run.exit_code == 2
    assert run.stderr.startswith(internal_error(injected(RuntimeError)))
    assert [write.label for write in run.writes] == [
        RECEIVED,
        "CommandEnvelope",
    ]


def test_keyboard_interrupt_passes_through(tmp_path, monkeypatch) -> None:
    with pytest.raises(KeyboardInterrupt):
        execute(
            scenario_named("inventory-audited"),
            tmp_path,
            monkeypatch,
            collector=FailingReadCollector(KeyboardInterrupt),
        )


def test_unexpected_error_with_vanished_audit_record_stores_failed_record(
    tmp_path,
    monkeypatch,
) -> None:
    scenario = scenario_named("inventory-audited")
    vanished = UUID(int=1)
    (tmp_path / "audit").mkdir()
    (tmp_path / "audit" / f"{vanished}.json").symlink_to(tmp_path / "gone")
    arguments, _ = scenario.build(tmp_path)

    run = run_cli(
        arguments,
        monkeypatch,
        collector=FailingReadCollector(RuntimeError),
        command_id=COMMAND_ID,
        clock=lambda: datetime(2026, 1, 1, tzinfo=UTC),
    )

    assert run.exit_code == 2
    assert run.stderr == (
        internal_error(injected(RuntimeError))
        + f"aura: audit event not found: {vanished}\n"
    )
    assert [write.label for write in run.writes] == [
        RECEIVED,
        "CommandEnvelope",
    ]
    record = JsonCommandStore(tmp_path / "commands").load(KEY)
    assert record.status is CommandState.FAILED


@pytest.mark.parametrize(
    "scenario",
    [scenario for scenario in SCENARIOS if "Proposal" in scenario.writes],
    ids=scenario_id,
)
def test_error_before_terminal_record_leaves_no_proposal(
    tmp_path,
    monkeypatch,
    scenario,
) -> None:
    error = RuntimeError("injected transition failure")
    transition = CommandStateMachine.transition

    def failing_transition(self, current, next_state):
        if next_state is CommandState.SUCCEEDED:
            raise error
        return transition(self, current, next_state)

    monkeypatch.setattr(CommandStateMachine, "transition", failing_transition)

    run, before = execute(scenario, tmp_path, monkeypatch)

    assert_internal_failure(run, tmp_path, before, error)
    assert proposals_of(tmp_path / "proposal", COMMAND_ID) == []
