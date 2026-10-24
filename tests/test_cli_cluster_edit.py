from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

import aura.cli as cli
from aura.cli import main
from aura.command_store import JsonCommandStore
from aura.contracts import CommandState
from aura.proposals import Proposal, ProposalType
from test_cli_propose import propose_arguments, stored_trace
from test_cluster_edit_proposal import fleet
from test_openshift_inventory_collector import FakeOpenShiftClient


def pipeline_run(name: str, pipeline: str, start: str, **params: str):
    return {
        "metadata": {"name": name, "namespace": "openshift-pipelines"},
        "spec": {
            "pipelineRef": {"name": pipeline},
            "params": [
                {"name": "cluster-name", "value": "example"},
                *({"name": k, "value": v} for k, v in params.items()),
            ],
        },
        "status": {
            "startTime": start,
            "conditions": [{"type": "Succeeded", "status": "True"}],
        },
    }


class FakeTektonClient:
    def __init__(self, **_: object) -> None:
        pass

    def list_pipeline_runs(self) -> list[dict[str, Any]]:
        return [
            pipeline_run(
                "post-provision-example-old",
                "post-provision",
                "2026-01-01T00:00:00Z",
                **{"htpasswd-provider-name": "PartnerIDP"},
            ),
            pipeline_run(
                "provision-example-new",
                "provision",
                "2026-01-02T00:00:00Z",
            ),
        ]

    def list_task_runs(self) -> list[dict[str, Any]]:
        return []

    def resource_version(self) -> str:
        return "pipelineruns=1;taskruns=1"


def run_cluster_edit(tmp_path: Path, monkeypatch, *roles: str) -> int:
    monkeypatch.setattr(
        cli,
        "OpenShiftCLIInventoryClient",
        lambda **_: FakeOpenShiftClient(),
    )
    monkeypatch.setattr(cli, "OpenShiftCLITektonClient", FakeTektonClient)
    return main(
        propose_arguments(tmp_path, *roles, command="cluster-edit"),
        command_id_factory=lambda: UUID(int=1),
        clock=lambda: datetime(2026, 1, 1, tzinfo=UTC),
    )


def test_cli_propose_cluster_edit_uses_latest_post_provision_run(
    tmp_path,
    capsys,
    monkeypatch,
) -> None:
    fleet(tmp_path)

    exit_code = run_cluster_edit(tmp_path, monkeypatch, "proposer")
    captured = capsys.readouterr()

    assert exit_code == 0, captured.err
    proposal = Proposal.model_validate_json(captured.out)
    assert proposal.proposal_type is ProposalType.CLUSTER_EDIT
    assert "+htpasswd-provider-name: PartnerIDP" in (proposal.patch or "")
    assert "PipelineRun post-provision-example-old" in proposal.summary
    command = JsonCommandStore(tmp_path / "commands").load("propose-key")
    assert command.status is CommandState.SUCCEEDED
    assert command.input["proposal_type"] == "cluster_edit"
    assert stored_trace(tmp_path).model_name == "cluster-edit-deterministic"


def test_cli_propose_cluster_edit_refuses_scoped_credentials(
    tmp_path,
    capsys,
    monkeypatch,
) -> None:
    fleet(tmp_path, template="crossplane-scoped")

    exit_code = run_cluster_edit(tmp_path, monkeypatch, "proposer")

    assert exit_code == 2
    assert capsys.readouterr().err == (
        "aura: proposal refused: cluster example uses scoped AWS "
        "credentials but metadata.yaml does not declare "
        "aws-credentials-mode\n"
    )
    command = JsonCommandStore(tmp_path / "commands").load("propose-key")
    assert command.status is CommandState.REFUSED


@pytest.mark.parametrize("roles", [(), ("viewer",), ("approver",)])
def test_cli_propose_cluster_edit_requires_proposer_or_admin(
    tmp_path,
    capsys,
    monkeypatch,
    roles,
) -> None:
    fleet(tmp_path)

    exit_code = run_cluster_edit(tmp_path, monkeypatch, *roles)

    assert exit_code == 2
    assert capsys.readouterr().err == (
        "aura: operator is not authorized to propose changes\n"
    )
