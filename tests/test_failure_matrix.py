from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from aura.collectors.git_inventory import GitInventoryError
from aura.collectors.git_staleness import ProposalRecordUnreadable
from aura.collectors.oc_cli import OpenShiftCLIError
from aura.contracts import AuditEventType, CommandState, EvidenceSnapshot
from aura.proposal_store import JsonProposalStore, ProposalNotFound
from failure_injection import WRITE_PHASES, run_cli
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


class FailingReadCollector:
    def __init__(self, error: type[Exception]) -> None:
        self._error = error

    def collect(self, **_: object) -> EvidenceSnapshot:
        raise self._error("injected read failure")


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
