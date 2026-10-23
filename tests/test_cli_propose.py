from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid5

import pytest

from aura.cli import main
from aura.collectors.git_inventory import GitInventoryCollector
from aura.command_store import JsonCommandStore
from aura.contracts import (
    AuditEvent,
    AuditEventType,
    CommandState,
    CommandType,
)
from aura.cwa import CWATrace
from aura.proposals import Proposal
from test_cli_diagnose_state import FakeClusterStateCollector
from test_cluster_state_collector import write_deprovisioned_cluster
from test_git_inventory_collector import commit_repository, write_cluster


COMMAND_ID = UUID(int=1)
CWA_SNAPSHOT_ID = uuid5(COMMAND_ID, f"cwa-snapshot:{UUID(int=40)}")
PROPOSAL_ID = uuid5(COMMAND_ID, f"proposal:{CWA_SNAPSHOT_ID}")


def cluster_state_collector(root: Path) -> FakeClusterStateCollector:
    return FakeClusterStateCollector(git_collector=GitInventoryCollector(root))


def fleet_clusters(tmp_path: Path, sentinel: str | None = None) -> Path:
    root = tmp_path / "fleet-clusters"
    if sentinel is not None:
        write_deprovisioned_cluster(root, sentinel)
        return root
    write_cluster(root)
    commit_repository(root)
    return root


def propose_arguments(
    tmp_path: Path,
    *roles: str,
    cluster: str = "example",
) -> list[str]:
    arguments = [
        "propose",
        "deprovision",
        "--cluster",
        cluster,
        "--fleet-clusters",
        str(tmp_path / "fleet-clusters"),
        "--openshift-kubeconfig",
        str(tmp_path / "kubeconfig"),
        "--snapshot-dir",
        str(tmp_path / "snapshots"),
        "--audit-dir",
        str(tmp_path / "audit"),
        "--cwa-dir",
        str(tmp_path / "cwa"),
        "--proposal-dir",
        str(tmp_path / "proposals"),
        "--operator",
        "operator@example.com",
        "--idempotency-key",
        "propose-key",
        "--command-dir",
        str(tmp_path / "commands"),
    ]
    for role in roles:
        arguments.extend(["--role", role])
    return arguments


def run_propose(
    arguments: list[str],
    collector: FakeClusterStateCollector,
) -> int:
    return main(
        arguments,
        collector=collector,
        command_id_factory=lambda: UUID(int=1),
        clock=lambda: datetime(2026, 1, 1, tzinfo=UTC),
    )


def audit_events(tmp_path: Path) -> dict[AuditEventType, AuditEvent]:
    events = [
        AuditEvent.model_validate_json(path.read_text())
        for path in (tmp_path / "audit").glob("*.json")
        if path.stat().st_size
    ]
    return {event.event_type: event for event in events}


def stored_trace(tmp_path: Path) -> CWATrace:
    return CWATrace.model_validate_json(
        next((tmp_path / "cwa" / "traces").glob("*.json")).read_text()
    )


def test_cli_propose_deprovision_stores_proposal(tmp_path, capsys) -> None:
    collector = cluster_state_collector(fleet_clusters(tmp_path))

    exit_code = run_propose(propose_arguments(tmp_path, "proposer"), collector)
    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured.err == ""
    proposal = Proposal.model_validate_json(captured.out)
    stored = Proposal.model_validate_json(
        (tmp_path / "proposals" / f"{proposal.proposal_id}.json").read_text()
    )
    assert stored == proposal
    assert proposal.cluster_name == "example"
    assert (
        tmp_path / "snapshots" / f"{proposal.evidence_snapshot_id}.json"
    ).exists()

    command = JsonCommandStore(tmp_path / "commands").load("propose-key")
    assert command.command_type is CommandType.PROPOSE_PATCH
    assert command.status is CommandState.SUCCEEDED
    assert command.input["proposal_type"] == "deprovision"

    trace = stored_trace(tmp_path)
    assert proposal.cwa_trace_id == trace.trace_id
    assert proposal.cwa_snapshot_id == trace.cwa_snapshot_id
    assert trace.model_provider == "aura"
    assert trace.model_name == "deprovision-sentinel-deterministic"
    assert trace.response_digest == proposal.proposal_digest

    events = audit_events(tmp_path)
    assert set(events) == {
        AuditEventType.COMMAND_RECEIVED,
        AuditEventType.EVIDENCE_COLLECTED,
        AuditEventType.CONTEXT_ASSEMBLED,
        AuditEventType.PROPOSAL_CREATED,
    }
    created = events[AuditEventType.PROPOSAL_CREATED]
    assert created.previous_event_id == (
        events[AuditEventType.CONTEXT_ASSEMBLED].event_id
    )
    assert created.data["proposal_id"] == str(proposal.proposal_id)
    assert created.data["proposal_digest"] == proposal.proposal_digest
    assert created.data["base_commit"] == proposal.base_commit


@pytest.mark.parametrize(
    ("roles", "expected_exit_code"),
    [
        ([], 2),
        (["viewer"], 2),
        (["approver"], 2),
        (["executor"], 2),
        (["proposer"], 0),
        (["admin"], 0),
        (["viewer", "proposer"], 0),
    ],
)
def test_cli_propose_deprovision_requires_proposer_or_admin(
    tmp_path,
    capsys,
    roles,
    expected_exit_code,
) -> None:
    collector = cluster_state_collector(fleet_clusters(tmp_path))

    exit_code = run_propose(propose_arguments(tmp_path, *roles), collector)
    captured = capsys.readouterr()

    assert exit_code == expected_exit_code
    if expected_exit_code:
        assert captured.err == (
            "aura: operator is not authorized to propose changes\n"
        )
        assert collector.calls == 0
        assert not (tmp_path / "proposals").exists()


def test_cli_propose_deprovision_refuses_existing_sentinel(
    tmp_path,
    capsys,
) -> None:
    collector = cluster_state_collector(
        fleet_clusters(tmp_path, sentinel="example-archive")
    )

    exit_code = run_propose(propose_arguments(tmp_path, "proposer"), collector)
    captured = capsys.readouterr()

    assert exit_code == 2
    assert captured.out == ""
    assert captured.err == (
        "aura: proposal refused: deprovision sentinel already exists: "
        "deprovision/example-archive\n"
    )
    assert not (tmp_path / "proposals").exists()
    command = JsonCommandStore(tmp_path / "commands").load("propose-key")
    assert command.status is CommandState.REFUSED
    assert stored_trace(tmp_path).response_digest is None
    events = audit_events(tmp_path)
    refused = events[AuditEventType.COMMAND_REFUSED]
    assert AuditEventType.PROPOSAL_CREATED not in events
    assert refused.previous_event_id == (
        events[AuditEventType.CONTEXT_ASSEMBLED].event_id
    )


@pytest.mark.parametrize(
    ("cluster", "error"),
    [
        ("Example", "--cluster must be a DNS-1123 label: Example"),
        ("example-", "--cluster must be a DNS-1123 label: example-"),
    ],
)
def test_cli_propose_deprovision_rejects_invalid_cluster_name(
    tmp_path,
    capsys,
    cluster,
    error,
) -> None:
    collector = cluster_state_collector(fleet_clusters(tmp_path))

    exit_code = run_propose(
        propose_arguments(tmp_path, "proposer", cluster=cluster),
        collector,
    )

    assert exit_code == 2
    assert capsys.readouterr().err == f"aura: {error}\n"
    assert collector.calls == 0


def test_cli_propose_deprovision_rejects_unusable_proposal_dir(
    tmp_path,
    capsys,
) -> None:
    collector = cluster_state_collector(fleet_clusters(tmp_path))
    (tmp_path / "proposals").write_text("", encoding="utf-8")

    exit_code = run_propose(propose_arguments(tmp_path, "proposer"), collector)

    assert exit_code == 2
    assert capsys.readouterr().err == (
        "aura: --proposal-dir is not a usable directory: "
        f"{tmp_path / 'proposals'}\n"
    )
    assert collector.calls == 0


@pytest.mark.parametrize(
    "flag",
    ["--snapshot-dir", "--audit-dir", "--cwa-dir", "--proposal-dir"],
)
def test_cli_propose_deprovision_requires_record_directories(
    tmp_path,
    flag,
) -> None:
    arguments = propose_arguments(tmp_path, "proposer")
    index = arguments.index(flag)
    del arguments[index : index + 2]

    with pytest.raises(SystemExit) as exit_info:
        main(arguments)

    assert exit_info.value.code == 2


def test_cli_propose_deprovision_rejects_reused_idempotency_key(
    tmp_path,
    capsys,
) -> None:
    collector = cluster_state_collector(fleet_clusters(tmp_path))
    arguments = propose_arguments(tmp_path, "proposer")
    assert run_propose(arguments, collector) == 0
    capsys.readouterr()

    exit_code = run_propose(arguments, collector)

    assert exit_code == 2
    assert capsys.readouterr().err == (
        "aura: idempotency key already succeeded: propose-key\n"
    )
    assert collector.calls == 1
    assert len(list((tmp_path / "proposals").glob("*.json"))) == 1
    assert json.loads(
        next((tmp_path / "proposals").glob("*.json")).read_text()
    )["cluster_name"] == "example"


@pytest.mark.parametrize(
    ("blocked", "failed_after"),
    [
        (Path("cwa") / "traces", AuditEventType.EVIDENCE_COLLECTED),
        (
            Path("audit")
            / f"{uuid5(COMMAND_ID, f'proposal_created:{PROPOSAL_ID}')}.json",
            AuditEventType.CONTEXT_ASSEMBLED,
        ),
        (
            Path("proposals") / f"{PROPOSAL_ID}.json",
            AuditEventType.PROPOSAL_CREATED,
        ),
    ],
)
def test_cli_propose_deprovision_stores_proposal_after_provenance(
    tmp_path,
    capsys,
    blocked,
    failed_after,
) -> None:
    collector = cluster_state_collector(fleet_clusters(tmp_path))
    (tmp_path / blocked).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / blocked).write_text("", encoding="utf-8")

    exit_code = run_propose(propose_arguments(tmp_path, "proposer"), collector)

    assert exit_code == 2
    assert capsys.readouterr().out == ""
    assert all(
        path.stat().st_size == 0
        for path in (tmp_path / "proposals").glob("*.json")
    )
    command = JsonCommandStore(tmp_path / "commands").load("propose-key")
    assert command.status is CommandState.FAILED
    events = audit_events(tmp_path)
    assert events[AuditEventType.COMMAND_FAILED].previous_event_id == (
        events[failed_after].event_id
    )
