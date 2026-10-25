from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

import aura.cli as cli
from aura.cli import main
from aura.command_store import JsonCommandStore
from aura.contracts import AuditEventType, CommandState
from aura.proposals import Proposal, ProposalType
from fake_scaffold import fake_scaffold, fleet_with_templates
from invariants import assert_command_invariants, command_chain
from aura.scaffold import ScaffoldRunError
from test_cli_propose import stored_trace
from test_scaffold_proposal import FakeOpenShiftClient


NEW = "new-cluster"
KEY = "scaffold-key"


def head(root: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def state(root: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain", "--ignored"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def scaffold_arguments(
    tmp_path: Path,
    *extra: str,
    cluster: str = NEW,
    roles: tuple[str, ...] = ("proposer",),
) -> list[str]:
    arguments = [
        "propose",
        "scaffold",
        "--cluster",
        cluster,
        "--region",
        "us-east-1",
        "--tier",
        "base",
        *extra,
        "--scaffold",
        str(tmp_path / "fleet-cluster-scaffold"),
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
        KEY,
        "--command-dir",
        str(tmp_path / "commands"),
    ]
    for role in roles:
        arguments.extend(["--role", role])
    return arguments


def run_scaffold(
    tmp_path: Path,
    monkeypatch,
    *extra: str,
    cluster: str = NEW,
    roles: tuple[str, ...] = ("proposer",),
) -> int:
    fleet_with_templates(tmp_path / "fleet-clusters")
    fake_scaffold(tmp_path / "fleet-cluster-scaffold")
    monkeypatch.setattr(
        cli,
        "OpenShiftCLIInventoryClient",
        lambda **_: FakeOpenShiftClient(("existing",), ("existing",)),
    )
    monkeypatch.setattr(cli, "kustomize_version", lambda: "v5.8.1")
    return main(
        scaffold_arguments(tmp_path, *extra, cluster=cluster, roles=roles),
        command_id_factory=lambda: UUID(int=1),
        clock=lambda: datetime(2026, 1, 1, tzinfo=UTC),
    )


def stored_command(tmp_path: Path):
    return JsonCommandStore(tmp_path / "commands").load(KEY)


def test_cli_proposes_a_scaffold(tmp_path, capsys, monkeypatch) -> None:
    exit_code = run_scaffold(
        tmp_path, monkeypatch, "--htpasswd-provider-name", "Acme Corp"
    )
    captured = capsys.readouterr()

    assert exit_code == 0, captured.err
    proposal = Proposal.model_validate_json(captured.out)
    assert proposal.proposal_type is ProposalType.SCAFFOLD
    assert proposal.cluster_name == NEW
    assert proposal.base_commit == head(tmp_path / "fleet-clusters")
    assert proposal.scaffold_commit == head(
        tmp_path / "fleet-cluster-scaffold"
    )
    assert "+htpasswd-provider-name: Acme Corp" in (proposal.patch or "")
    [stored] = (tmp_path / "proposals").glob("*.json")
    assert Proposal.model_validate_json(stored.read_text()) == proposal
    trace = stored_trace(tmp_path)
    assert trace.model_name == "scaffold-deterministic"
    assert trace.response_digest == proposal.proposal_digest
    assert [
        event.event_type
        for event in command_chain(tmp_path / "audit", UUID(int=1))
    ] == [
        AuditEventType.COMMAND_RECEIVED,
        AuditEventType.EVIDENCE_COLLECTED,
        AuditEventType.CONTEXT_ASSEMBLED,
        AuditEventType.PROPOSAL_CREATED,
    ]
    command = stored_command(tmp_path)
    assert command.status is CommandState.SUCCEEDED
    assert command.input["proposal_type"] == "scaffold"
    assert command.input["cluster_name"] == NEW
    assert command.input["scaffold_request"] == {
        "cloud": "aws",
        "cluster_name": NEW,
        "region": "us-east-1",
        "tier": "base",
        "options": [["--htpasswd-provider-name", "Acme Corp"]],
    }


def test_cli_passes_operator_flags_in_allow_list_order(
    tmp_path,
    capsys,
    monkeypatch,
) -> None:
    exit_code = run_scaffold(
        tmp_path,
        monkeypatch,
        "--htpasswd-provider-name",
        "Acme Corp",
        "--zones",
        "us-east-1a",
        "--environment",
        "production",
        "--topology",
        "sno",
    )
    captured = capsys.readouterr()

    assert exit_code == 0, captured.err
    expected = [
        ["--topology", "sno"],
        ["--environment", "production"],
        ["--zones", "us-east-1a"],
        ["--htpasswd-provider-name", "Acme Corp"],
    ]
    assert stored_command(tmp_path).input["scaffold_request"]["options"] == (
        expected
    )
    run = Proposal.model_validate_json(captured.out).scaffold_run
    assert run is not None
    assert run.arguments[7:15] == [part for pair in expected for part in pair]


def test_cli_leaves_both_checkouts_unchanged(
    tmp_path,
    capsys,
    monkeypatch,
) -> None:
    fleet_with_templates(tmp_path / "fleet-clusters")
    fake_scaffold(tmp_path / "fleet-cluster-scaffold")
    before = (
        state(tmp_path / "fleet-clusters"),
        state(tmp_path / "fleet-cluster-scaffold"),
    )
    monkeypatch.setattr(
        cli,
        "OpenShiftCLIInventoryClient",
        lambda **_: FakeOpenShiftClient(("existing",), ("existing",)),
    )
    monkeypatch.setattr(cli, "kustomize_version", lambda: "v5.8.1")

    exit_code = main(
        scaffold_arguments(tmp_path),
        command_id_factory=lambda: UUID(int=1),
        clock=lambda: datetime(2026, 1, 1, tzinfo=UTC),
    )

    assert exit_code == 0, capsys.readouterr().err
    assert (
        state(tmp_path / "fleet-clusters"),
        state(tmp_path / "fleet-cluster-scaffold"),
    ) == before


def test_cli_refuses_a_name_in_use(tmp_path, capsys, monkeypatch) -> None:
    exit_code = run_scaffold(tmp_path, monkeypatch, cluster="existing")

    assert exit_code == 2
    assert capsys.readouterr().err.startswith(
        "aura: proposal refused: cluster existing already has a definition "
        "at provision/existing"
    )
    assert stored_command(tmp_path).status is CommandState.REFUSED
    assert not list((tmp_path / "proposals").glob("*.json"))
    assert list((tmp_path / "snapshots").glob("*.json"))


@pytest.mark.parametrize("roles", [(), ("viewer",), ("approver",)])
def test_cli_scaffold_requires_proposer_or_admin(
    tmp_path,
    capsys,
    monkeypatch,
    roles,
) -> None:
    exit_code = run_scaffold(tmp_path, monkeypatch, roles=roles)

    assert exit_code == 2
    assert capsys.readouterr().err == (
        "aura: operator is not authorized to propose changes\n"
    )
    assert not (tmp_path / "snapshots").exists() or not list(
        (tmp_path / "snapshots").glob("*.json")
    )


def test_cli_rejects_an_invalid_cluster_name(
    tmp_path,
    capsys,
    monkeypatch,
) -> None:
    exit_code = run_scaffold(tmp_path, monkeypatch, cluster="Bad_Name")

    assert exit_code == 2
    assert capsys.readouterr().err == (
        "aura: --cluster must be a DNS-1123 label: Bad_Name\n"
    )
    assert not (tmp_path / "commands").exists()


def test_cli_requires_a_scaffold_checkout(tmp_path, capsys) -> None:
    arguments = scaffold_arguments(tmp_path)
    index = arguments.index("--scaffold")
    del arguments[index : index + 2]

    with pytest.raises(SystemExit) as exit_info:
        main(arguments)

    assert exit_info.value.code == 2
    assert "the following arguments are required: --scaffold" in (
        capsys.readouterr().err
    )


def test_cli_fails_when_the_scaffold_run_fails(
    tmp_path,
    capsys,
    monkeypatch,
) -> None:
    exit_code = run_scaffold(
        tmp_path, monkeypatch, "--environment", "binary"
    )

    assert exit_code == 2
    assert capsys.readouterr().err == (
        f"aura: scaffold output provision/{NEW}/blob.yaml is not UTF-8\n"
    )
    assert_failed_after_evidence(tmp_path)


def assert_failed_after_evidence(tmp_path: Path) -> None:
    command = assert_command_invariants(
        command_dir=tmp_path / "commands",
        idempotency_key=KEY,
        audit_dir=tmp_path / "audit",
        cwa_dir=tmp_path / "cwa",
        proposal_dir=tmp_path / "proposals",
    )
    assert command is not None
    assert command.status is CommandState.FAILED
    assert not list((tmp_path / "proposals").glob("*.json"))
    received, collected, failed = command_chain(
        tmp_path / "audit", UUID(int=1)
    )
    assert [received.event_type, collected.event_type, failed.event_type] == [
        AuditEventType.COMMAND_RECEIVED,
        AuditEventType.EVIDENCE_COLLECTED,
        AuditEventType.COMMAND_FAILED,
    ]
    assert collected.previous_event_id == received.event_id
    assert failed.previous_event_id == collected.event_id


class FailingDefinitionCheck:
    def __init__(self, *_: object, **__: object) -> None:
        pass

    def check(self, **_: object) -> list[str]:
        raise ScaffoldRunError("git read-tree failed for the definition check")


def test_cli_fails_when_the_definition_check_fails(
    tmp_path,
    capsys,
    monkeypatch,
) -> None:
    monkeypatch.setattr(cli, "GitDefinitionCheck", FailingDefinitionCheck)

    exit_code = run_scaffold(tmp_path, monkeypatch)

    assert exit_code == 2
    assert capsys.readouterr().err == (
        "aura: git read-tree failed for the definition check\n"
    )
    assert_failed_after_evidence(tmp_path)
