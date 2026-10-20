from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from aura.collectors.git_staleness import GitProposalStaleness
from aura.proposals import Proposal
from test_git_inventory_collector import commit_repository, write_cluster
from test_proposals import added_file, make_proposal


def deprovision_proposal(base_commit: str) -> Proposal:
    return make_proposal(
        "deprovision",
        cluster_name="example",
        base_commit=base_commit,
        patch=added_file("deprovision/example-archive"),
        commit_message="chore: deprovision example",
    )


def git(root: Path, *arguments: str) -> None:
    subprocess.run(["git", "-C", str(root), *arguments], check=True)


@pytest.fixture
def repository(tmp_path: Path) -> tuple[Path, str]:
    write_cluster(tmp_path)
    write_cluster(tmp_path, cluster_name="other")
    return tmp_path, commit_repository(tmp_path)


def test_staleness_reports_fresh_proposal_at_base(repository) -> None:
    root, base = repository

    result = GitProposalStaleness(root).check(deprovision_proposal(base))

    assert (result.stale, result.head, result.reasons) == (False, base, ())


def test_staleness_ignores_changes_to_other_clusters(repository) -> None:
    root, base = repository
    (root / "provision" / "other" / "metadata.yaml").write_text(
        "platform: aws\n", encoding="utf-8"
    )
    head = commit_repository(root)

    result = GitProposalStaleness(root).check(deprovision_proposal(base))

    assert (result.stale, result.head) == (False, head)


@pytest.mark.parametrize(
    "path",
    ["provision/example/metadata.yaml", "deprovision/example-archive"],
)
def test_staleness_reports_changes_to_target_cluster(repository, path) -> None:
    root, base = repository
    (root / path).parent.mkdir(parents=True, exist_ok=True)
    (root / path).write_text("changed\n", encoding="utf-8")
    commit_repository(root)

    result = GitProposalStaleness(root).check(deprovision_proposal(base))

    assert result.stale is True
    assert result.reasons == (f"{path} changed since base commit {base}",)


def test_staleness_reports_base_outside_history(repository) -> None:
    root, _ = repository
    git(root, "checkout", "-q", "-b", "side")
    (root / "provision" / "other" / "side.yaml").write_text("x\n", encoding="utf-8")
    side = commit_repository(root)
    git(root, "checkout", "-q", "-")

    result = GitProposalStaleness(root).check(deprovision_proposal(side))

    assert result.stale is True
    assert result.reasons == (
        f"base commit {side} is not an ancestor of HEAD {result.head}",
    )


def test_staleness_reports_unknown_base(repository) -> None:
    root, _ = repository
    unknown = "f" * 40

    result = GitProposalStaleness(root).check(deprovision_proposal(unknown))

    assert result.stale is True
    assert result.reasons == (
        f"base commit {unknown} is not in the checkout history",
    )


def test_staleness_skips_git_check_for_reruns(repository) -> None:
    root, base = repository

    result = GitProposalStaleness(root).check(
        make_proposal("post_provision_rerun")
    )

    assert (result.stale, result.head, result.reasons) == (False, base, ())
