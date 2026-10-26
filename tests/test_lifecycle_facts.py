"""One test per lifecycle fact in docs/plans/05-phase-2-proposals.md.

Each test is named for its row and asserts the trigger effect and
commit-message rule the generator reports for that fact. The pull
request, other-branch, and merge-method rows have no Aura behavior; the
table states why.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from aura.contracts import ARCHIVE_SUFFIX
from aura.proposals import SKIP_PROVISION, TriggerEffect, patch_changes
from test_cluster_edit_proposal import evidence as edit_evidence
from test_cluster_edit_proposal import fleet as edit_fleet
from test_cluster_edit_proposal import generate as generate_edit
from test_cluster_edit_proposal import post_provision
from test_deprovision_proposal import cluster_state
from test_deprovision_proposal import generate as generate_deprovision
from test_git_inventory_collector import commit_repository, write_cluster
from test_proposals import CLUSTER, added_file, make_proposal
from test_rerun_proposal import FULL_METADATA, LATEST
from test_rerun_proposal import evidence as rerun_evidence
from test_rerun_proposal import fleet as rerun_fleet
from test_rerun_proposal import generate as generate_rerun
from test_scaffold_proposal import FakeRunner
from test_scaffold_proposal import evidence as scaffold_evidence
from test_scaffold_proposal import generate as generate_scaffold


def deprovision(tmp_path: Path):
    write_cluster(tmp_path)
    commit_repository(tmp_path)
    return generate_deprovision(cluster_state(tmp_path))


def test_push_adding_a_provision_file_starts_pre_provision(
    tmp_path: Path,
) -> None:
    proposal = generate_scaffold(scaffold_evidence(tmp_path), FakeRunner())

    assert proposal.trigger_effect == [
        TriggerEffect.PRE_PROVISION,
        TriggerEffect.PROVISION,
        TriggerEffect.POST_PROVISION,
    ]
    assert {path for path, _ in patch_changes(proposal.patch or "")}
    assert all(
        change == "added" and path.startswith("provision/new-cluster/")
        for path, change in patch_changes(proposal.patch or "")
    )
    assert SKIP_PROVISION not in (proposal.commit_message or "")


def test_head_commit_skip_provision_suppresses_pre_provision(
    tmp_path: Path,
) -> None:
    proposal = generate_edit(
        edit_evidence(
            edit_fleet(tmp_path),
            runs=(post_provision(**{"htpasswd-provider-name": "Idp"}),),
        )
    )

    assert patch_changes(proposal.patch or "") == [
        ("provision/example/metadata.yaml", "added")
    ]
    assert proposal.trigger_effect == []
    assert SKIP_PROVISION in (proposal.commit_message or "")
    assert any("head commit" in risk for risk in proposal.risks)


def test_push_adding_a_deprovision_file_starts_deprovision(
    tmp_path: Path,
) -> None:
    proposal = deprovision(tmp_path)

    assert patch_changes(proposal.patch or "") == [
        (f"deprovision/example{ARCHIVE_SUFFIX}", "added")
    ]
    assert TriggerEffect.DEPROVISION in proposal.trigger_effect
    assert ARCHIVE_SUFFIX not in proposal.cluster_name


def test_successful_deprovision_runs_the_archive_action(
    tmp_path: Path,
) -> None:
    proposal = deprovision(tmp_path)

    assert proposal.trigger_effect == [
        TriggerEffect.DEPROVISION,
        TriggerEffect.ARCHIVE_ACTION,
    ]
    assert "standalone commit" in (proposal.commit_message or "")


def test_sentinel_without_archive_suffix_is_refused() -> None:
    with pytest.raises(
        ValidationError,
        match=f"must add only deprovision/{CLUSTER}{ARCHIVE_SUFFIX}",
    ):
        make_proposal(patch=added_file(f"deprovision/{CLUSTER}"))


def test_modify_only_push_starts_no_pipeline(tmp_path: Path) -> None:
    proposal = generate_edit(
        edit_evidence(edit_fleet(tmp_path, "htpasswd-provider-name: Idp\n"))
    )

    assert {change for _, change in patch_changes(proposal.patch or "")} == {
        "modified"
    }
    assert proposal.trigger_effect == []
    assert SKIP_PROVISION not in (proposal.commit_message or "")


def test_post_provision_request_starts_post_provision(tmp_path: Path) -> None:
    proposal = generate_rerun(
        rerun_evidence(rerun_fleet(tmp_path, FULL_METADATA), LATEST)
    )

    assert proposal.trigger_effect == [TriggerEffect.POST_PROVISION]
    assert proposal.patch is None
    assert proposal.commit_message is None
