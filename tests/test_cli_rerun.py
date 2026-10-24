from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

import aura.cli as cli
from aura.cli import main
from aura.command_store import JsonCommandStore
from aura.contracts import CommandState, CommandType
from aura.proposals import Proposal, ProposalType
from test_cli_propose import propose_arguments, stored_trace
from test_cluster_edit_proposal import fleet
from test_openshift_inventory_collector import FakeOpenShiftClient
from test_pipeline_definitions import POST_PROVISION, FakePipelineClient
from test_rerun_proposal import FULL_METADATA, LATEST, OLDER, RunsClient, run


def run_rerun(
    tmp_path: Path,
    monkeypatch,
    *roles: str,
    runs: tuple = (OLDER, LATEST),
    pipelines: tuple = (POST_PROVISION,),
    extra: tuple[str, ...] = (),
) -> int:
    monkeypatch.setattr(
        cli,
        "OpenShiftCLIInventoryClient",
        lambda **_: FakeOpenShiftClient(),
    )
    monkeypatch.setattr(
        cli,
        "OpenShiftCLITektonClient",
        lambda **_: RunsClient(*runs),
    )
    monkeypatch.setattr(
        cli,
        "OpenShiftCLIPipelineClient",
        lambda **_: FakePipelineClient(*pipelines),
    )
    return main(
        [
            *propose_arguments(
                tmp_path, *roles, command="post-provision-rerun"
            ),
            *extra,
        ],
        command_id_factory=lambda: UUID(int=1),
        clock=lambda: datetime(2026, 1, 1, tzinfo=UTC),
    )


def stored_command(tmp_path: Path):
    return JsonCommandStore(tmp_path / "commands").load("propose-key")


def test_cli_proposes_a_rerun_of_the_latest_run(
    tmp_path,
    capsys,
    monkeypatch,
) -> None:
    fleet(tmp_path, FULL_METADATA)

    exit_code = run_rerun(tmp_path, monkeypatch, "proposer")
    captured = capsys.readouterr()

    assert exit_code == 0, captured.err
    proposal = Proposal.model_validate_json(captured.out)
    assert proposal.proposal_type is ProposalType.POST_PROVISION_RERUN
    assert "post-provision-example-new" in proposal.summary
    command = stored_command(tmp_path)
    assert command.command_type is CommandType.PROPOSE_PIPELINE
    assert command.status is CommandState.SUCCEEDED
    assert command.input["proposal_type"] == "post_provision_rerun"
    assert command.input["pipeline_run"] is None
    assert stored_trace(tmp_path).model_name == (
        "post-provision-rerun-deterministic"
    )


def test_cli_reruns_a_named_pipeline_run(
    tmp_path,
    capsys,
    monkeypatch,
) -> None:
    fleet(tmp_path, FULL_METADATA)

    exit_code = run_rerun(
        tmp_path,
        monkeypatch,
        "proposer",
        extra=("--pipeline-run", "post-provision-example-old"),
    )

    assert exit_code == 0
    proposal = Proposal.model_validate_json(capsys.readouterr().out)
    assert {"name": "tier", "value": "premium"} in (
        proposal.pipeline_run["spec"]["params"]
    )
    assert stored_command(tmp_path).input["pipeline_run"] == (
        "post-provision-example-old"
    )


def test_cli_refuses_a_rerun_while_the_latest_run_is_running(
    tmp_path,
    capsys,
    monkeypatch,
) -> None:
    fleet(tmp_path, FULL_METADATA)
    running = run(
        "post-provision-example-new",
        "2026-01-02T00:00:00Z",
        succeeded=None,
    )

    exit_code = run_rerun(
        tmp_path, monkeypatch, "proposer", runs=(OLDER, running)
    )

    assert exit_code == 2
    assert capsys.readouterr().err == (
        "aura: proposal refused: PipelineRun post-provision-example-new "
        "has not finished\n"
    )
    assert stored_command(tmp_path).status is CommandState.REFUSED


def test_cli_fails_when_the_pipeline_definition_is_missing(
    tmp_path,
    capsys,
    monkeypatch,
) -> None:
    fleet(tmp_path, FULL_METADATA)

    exit_code = run_rerun(tmp_path, monkeypatch, "proposer", pipelines=())

    assert exit_code == 2
    assert capsys.readouterr().err == (
        "aura: requested Pipelines not found: ['post-provision']\n"
    )
    assert stored_command(tmp_path).status is CommandState.FAILED


@pytest.mark.parametrize("roles", [(), ("viewer",), ("approver",)])
def test_cli_rerun_requires_proposer_or_admin(
    tmp_path,
    capsys,
    monkeypatch,
    roles,
) -> None:
    fleet(tmp_path, FULL_METADATA)

    exit_code = run_rerun(tmp_path, monkeypatch, *roles)

    assert exit_code == 2
    assert capsys.readouterr().err == (
        "aura: operator is not authorized to propose changes\n"
    )


def test_cli_rejects_a_blank_pipeline_run(
    tmp_path,
    capsys,
    monkeypatch,
) -> None:
    exit_code = run_rerun(
        tmp_path, monkeypatch, "proposer", extra=("--pipeline-run", " ")
    )

    assert exit_code == 2
    assert capsys.readouterr().err == (
        "aura: --pipeline-run must not be blank\n"
    )
