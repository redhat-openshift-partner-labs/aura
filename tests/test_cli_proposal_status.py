from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from aura.cli import main
from aura.command_store import JsonCommandStore
from aura.contracts import CommandState, CommandType, EvidenceSnapshot
from aura.proposal_store import JsonProposalStore
from test_git_inventory_collector import commit_repository, write_cluster
from test_git_staleness import deprovision_proposal


def fleet_clusters(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "fleet-clusters"
    write_cluster(root)
    return root, commit_repository(root)


def status_arguments(
    tmp_path: Path,
    *roles: str,
    proposal_ids: tuple[UUID, ...] = (),
) -> list[str]:
    arguments = [
        "proposal",
        "status",
        "--fleet-clusters",
        str(tmp_path / "fleet-clusters"),
        "--proposal-dir",
        str(tmp_path / "proposals"),
        "--operator",
        "operator@example.com",
        "--idempotency-key",
        "status-key",
        "--command-dir",
        str(tmp_path / "commands"),
    ]
    for proposal_id in proposal_ids:
        arguments.extend(["--proposal-id", str(proposal_id)])
    for role in roles:
        arguments.extend(["--role", role])
    return arguments


def run_status(arguments: list[str]) -> int:
    return main(
        arguments,
        command_id_factory=lambda: UUID(int=1),
        clock=lambda: datetime(2026, 1, 1, tzinfo=UTC),
    )


def test_cli_proposal_status_reports_stale_proposal(tmp_path, capsys) -> None:
    root, base = fleet_clusters(tmp_path)
    JsonProposalStore(tmp_path / "proposals").save(deprovision_proposal(base))
    (root / "provision" / "example" / "metadata.yaml").write_text(
        "changed\n", encoding="utf-8"
    )
    head = commit_repository(root)

    exit_code = run_status(status_arguments(tmp_path))
    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured.err == ""
    snapshot = EvidenceSnapshot.model_validate_json(captured.out)
    assert snapshot.source_version == head
    [item] = snapshot.items
    assert item.data["proposal_id"] == str(UUID(int=500))
    assert item.data["stale"] is True
    command = JsonCommandStore(tmp_path / "commands").load("status-key")
    assert command.command_type is CommandType.PROPOSAL_STATUS
    assert command.status is CommandState.SUCCEEDED
    assert command.input["proposal_ids"] == []


@pytest.mark.parametrize(
    ("roles", "expected_exit_code"),
    [
        ([], 0),
        (["viewer"], 0),
        (["proposer"], 0),
        (["admin"], 0),
        (["approver"], 2),
        (["executor"], 2),
    ],
)
def test_cli_proposal_status_authorizes_viewer_proposer_and_admin(
    tmp_path,
    capsys,
    roles,
    expected_exit_code,
) -> None:
    fleet_clusters(tmp_path)

    exit_code = run_status(status_arguments(tmp_path, *roles))
    captured = capsys.readouterr()

    assert exit_code == expected_exit_code
    if expected_exit_code:
        assert captured.err == (
            "aura: operator is not authorized to check proposal staleness\n"
        )
        assert captured.out == ""


def test_cli_proposal_status_fails_for_missing_proposal(
    tmp_path,
    capsys,
) -> None:
    fleet_clusters(tmp_path)

    exit_code = run_status(
        status_arguments(tmp_path, proposal_ids=(UUID(int=9),))
    )

    assert exit_code == 2
    assert capsys.readouterr().err == (
        f"aura: proposal not found: {UUID(int=9)}\n"
    )
    command = JsonCommandStore(tmp_path / "commands").load("status-key")
    assert command.status is CommandState.FAILED
    assert command.input["proposal_ids"] == [str(UUID(int=9))]


def test_cli_proposal_status_fails_for_tampered_proposal(
    tmp_path,
    capsys,
) -> None:
    _, base = fleet_clusters(tmp_path)
    path = JsonProposalStore(tmp_path / "proposals").save(
        deprovision_proposal(base)
    )
    path.chmod(0o600)
    path.write_text(
        path.read_text(encoding="utf-8").replace("example", "examples"),
        encoding="utf-8",
    )

    exit_code = run_status(status_arguments(tmp_path))

    assert exit_code == 2
    assert capsys.readouterr().err == (
        f"aura: proposal record is unreadable: {path.name}\n"
    )


def test_cli_proposal_status_rejects_invalid_proposal_id(tmp_path) -> None:
    arguments = status_arguments(tmp_path)
    arguments.extend(["--proposal-id", "not-a-uuid"])

    with pytest.raises(SystemExit) as exit_info:
        main(arguments)

    assert exit_info.value.code == 2
