from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from aura.collectors.git_inventory import GitInventoryError
from aura.contracts import AuditEventType, CommandState, EvidenceSnapshot
from aura.proposal_store import JsonProposalStore
from failure_injection import run_cli
from invariants import (
    assert_command_invariants,
    command_chain,
    proposals_of,
)
from test_cli_diagnose_state import FakeClusterStateCollector
from test_cli_propose import cluster_state_collector, fleet_clusters
from test_git_inventory_collector import commit_repository, write_cluster
from test_git_staleness import deprovision_proposal


COMMAND_ID = UUID(int=7)
KEY = "matrix-key"
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


def inventory_minimal(root: Path):
    return (
        ["inventory", "list", "--fleet-clusters", str(root), *operator(root)],
        FakeClusterStateCollector(),
    )


def inventory_assembled(root: Path):
    return (
        [
            "inventory",
            "list",
            "--fleet-clusters",
            str(root),
            *records(root, "snapshot", "audit", "cwa", "diagnosis"),
            *operator(root),
        ],
        FakeClusterStateCollector(),
    )


def workflow(root: Path):
    return (
        [
            "workflow",
            "inspect",
            "--openshift-kubeconfig",
            str(root / "kubeconfig"),
            *records(root, "snapshot", "audit"),
            *operator(root),
        ],
        FakeClusterStateCollector(),
    )


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


def proposal_status(root: Path):
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
            *records(root, "proposal", "snapshot", "audit"),
            *operator(root),
        ],
        None,
    )


SCENARIOS = [
    Scenario("inventory-minimal", inventory_minimal, ("CommandEnvelope",)),
    Scenario("inventory-assembled", inventory_assembled, DIAGNOSED),
    Scenario(
        "workflow",
        workflow,
        (RECEIVED, *EVIDENCE, "CommandEnvelope"),
    ),
    Scenario("diagnose", diagnose(True), DIAGNOSED),
    Scenario(
        "diagnose-refused",
        diagnose(False),
        REFUSED,
        CommandState.REFUSED,
    ),
    Scenario(
        "propose",
        propose(None),
        (
            RECEIVED,
            *EVIDENCE,
            *ASSEMBLED,
            *CONTEXT,
            "AuditEvent:proposal_created",
            "Proposal",
            "CommandEnvelope",
        ),
    ),
    Scenario(
        "propose-refused",
        propose("example-archive"),
        REFUSED,
        CommandState.REFUSED,
    ),
    Scenario(
        "proposal-status",
        proposal_status,
        (RECEIVED, *EVIDENCE, "CommandEnvelope"),
    ),
]


class FailingReadCollector:
    def collect(self, **_: object) -> EvidenceSnapshot:
        raise GitInventoryError("injected read failure")


def check(root: Path, *, expect_record: bool = True):
    return assert_command_invariants(
        command_dir=root / "commands",
        idempotency_key=KEY,
        audit_dir=root / "audit",
        cwa_dir=root / "cwa",
        proposal_dir=root / "proposal",
        expect_record=expect_record,
    )


def execute(scenario: Scenario, root: Path, monkeypatch, fail_at=None):
    arguments, collector = scenario.build(root)
    return run_cli(
        arguments,
        monkeypatch,
        collector=collector,
        command_id=COMMAND_ID,
        clock=lambda: datetime(2026, 1, 1, tzinfo=UTC),
        fail_at=fail_at,
    )


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda item: item.name)
def test_clean_run_writes_in_documented_order(
    tmp_path,
    monkeypatch,
    scenario,
) -> None:
    run = execute(scenario, tmp_path, monkeypatch)

    assert run.exit_code == scenario.exit_code, run.stderr
    assert [write.label for write in run.writes] == list(scenario.writes)
    assert check(tmp_path).status is scenario.status


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda item: item.name)
def test_each_write_failure_fails_closed(
    tmp_path,
    monkeypatch,
    scenario,
) -> None:
    for index, label in enumerate(scenario.writes):
        case = f"{scenario.name}: write {index} ({label}) fails"
        root = tmp_path / str(index)
        root.mkdir()

        run = execute(scenario, root, monkeypatch, fail_at=index)

        assert run.exit_code == 2, case
        assert run.stdout == "", case
        assert "injected write failure" in run.stderr, case
        final_write = index == len(scenario.writes) - 1
        record = check(root, expect_record=not final_write)
        if final_write:
            assert record is None, case
            continue
        assert record.status is CommandState.FAILED, case
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


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda item: item.name)
def test_evidence_read_failure_fails_closed(
    tmp_path,
    monkeypatch,
    scenario,
) -> None:
    arguments, _ = scenario.build(tmp_path)

    run = run_cli(
        arguments,
        monkeypatch,
        collector=FailingReadCollector(),
        command_id=COMMAND_ID,
        clock=lambda: datetime(2026, 1, 1, tzinfo=UTC),
    )

    assert run.exit_code == 2
    assert run.stderr == "aura: injected read failure\n"
    assert check(tmp_path).status is CommandState.FAILED
    if scenario.audited:
        assert [
            event.event_type
            for event in command_chain(tmp_path / "audit", COMMAND_ID)
        ] == [AuditEventType.COMMAND_RECEIVED, AuditEventType.COMMAND_FAILED]
