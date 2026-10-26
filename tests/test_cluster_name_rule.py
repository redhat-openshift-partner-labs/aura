from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from aura.services.proposal import ProposalRefused
from failure_injection import run_cli
from test_failure_matrix import (
    COMMAND_ID,
    cluster_edit,
    propose,
    rerun,
    scaffold,
)
from test_proposals import TYPE_FIELDS, added_file, make_proposal
from test_rerun_proposal import LATEST
from test_scaffold_proposal import (
    FakeRunner,
    evidence,
    generate,
    request,
)


ARCHIVE_NAMES = ("example-archive", "example-archive-2")
PROPOSE_BUILDERS = {
    "deprovision": propose(None),
    "cluster-edit": cluster_edit(),
    "post-provision-rerun": rerun(LATEST),
    "scaffold": scaffold("new-cluster"),
}


@pytest.mark.parametrize("name", ARCHIVE_NAMES)
@pytest.mark.parametrize("proposal_type", sorted(TYPE_FIELDS))
def test_proposal_rejects_cluster_name_containing_archive(
    proposal_type: str,
    name: str,
) -> None:
    with pytest.raises(
        ValidationError,
        match="cluster_name must not contain -archive",
    ):
        make_proposal(proposal_type, cluster_name=name)


def test_proposal_accepts_archive_without_the_hyphen_prefix() -> None:
    proposal = make_proposal(
        cluster_name="archive-one",
        commit_message="chore: deprovision archive-one",
        patch=added_file("deprovision/archive-one-archive"),
    )

    assert proposal.cluster_name == "archive-one"


@pytest.mark.parametrize("name", ARCHIVE_NAMES)
@pytest.mark.parametrize("subcommand", sorted(PROPOSE_BUILDERS))
def test_cli_propose_rejects_cluster_containing_archive(
    tmp_path: Path,
    monkeypatch,
    subcommand: str,
    name: str,
) -> None:
    arguments, collector = PROPOSE_BUILDERS[subcommand](tmp_path)
    arguments[arguments.index("--cluster") + 1] = name

    run = run_cli(
        arguments,
        monkeypatch,
        collector=collector,
        command_id=COMMAND_ID,
        clock=lambda: datetime(2026, 1, 1, tzinfo=UTC),
    )

    assert run.exit_code == 2
    assert run.stderr == f"aura: --cluster must not contain -archive: {name}\n"
    assert run.writes == []
    assert not (tmp_path / "commands").exists()


@pytest.mark.parametrize("name", ARCHIVE_NAMES)
def test_scaffold_refuses_cluster_containing_archive(
    tmp_path: Path,
    name: str,
) -> None:
    runner = FakeRunner()

    with pytest.raises(ProposalRefused) as refusal:
        generate(evidence(tmp_path, request(name)), runner)

    assert f"cluster name {name} must not contain -archive" in (
        refusal.value.reasons
    )
    assert runner.calls == []
