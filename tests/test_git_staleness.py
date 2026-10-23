from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from aura.collectors.git_staleness import (
    GitProposalStaleness,
    ProposalRecordUnreadable,
    ProposalStalenessCollector,
)
from aura.proposal_store import JsonProposalStore, ProposalNotFound
from aura.proposals import Proposal
from test_git_inventory_collector import commit_repository, write_cluster
from test_proposals import added_file, make_proposal


def deprovision_proposal(
    base_commit: str,
    cluster: str = "example",
    proposal_id: UUID = UUID(int=500),
) -> Proposal:
    return make_proposal(
        "deprovision",
        proposal_id=proposal_id,
        cluster_name=cluster,
        base_commit=base_commit,
        patch=added_file(f"deprovision/{cluster}-archive"),
        commit_message=f"chore: deprovision {cluster}",
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


def collect_staleness(
    root: Path,
    proposal_dir: Path,
    proposal_ids: list[UUID] | None = None,
):
    return ProposalStalenessCollector(
        proposal_store=JsonProposalStore(proposal_dir),
        staleness=GitProposalStaleness(root),
        proposal_ids=proposal_ids,
    ).collect(
        command_id=UUID(int=1),
        collected_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def test_staleness_collector_reports_each_stored_proposal(
    repository,
    tmp_path,
) -> None:
    root, base = repository
    store = JsonProposalStore(tmp_path / "proposals")
    store.save(deprovision_proposal(base))
    store.save(deprovision_proposal(base, "other", UUID(int=501)))
    rerun = make_proposal("post_provision_rerun", proposal_id=UUID(int=502))
    store.save(rerun)
    (root / "provision" / "example" / "metadata.yaml").write_text(
        "changed\n", encoding="utf-8"
    )
    head = commit_repository(root)

    snapshot = collect_staleness(root, tmp_path / "proposals")

    assert snapshot.collector == "proposal_staleness"
    assert snapshot.source == "fleet-clusters"
    assert snapshot.source_version == head
    assert [item.kind for item in snapshot.items] == [
        "proposal_staleness"
    ] * 3
    assert [item.data for item in snapshot.items] == [
        {
            "proposal_id": str(UUID(int=500)),
            "proposal_type": "deprovision",
            "cluster_name": "example",
            "proposal_digest": deprovision_proposal(base).proposal_digest,
            "base_commit": base,
            "head": head,
            "git_checked": True,
            "stale": True,
            "reasons": [
                "provision/example/metadata.yaml changed since base "
                f"commit {base}"
            ],
        },
        {
            "proposal_id": str(UUID(int=501)),
            "proposal_type": "deprovision",
            "cluster_name": "other",
            "proposal_digest": deprovision_proposal(
                base, "other", UUID(int=501)
            ).proposal_digest,
            "base_commit": base,
            "head": head,
            "git_checked": True,
            "stale": False,
            "reasons": [],
        },
        {
            "proposal_id": str(UUID(int=502)),
            "proposal_type": "post_provision_rerun",
            "cluster_name": rerun.cluster_name,
            "proposal_digest": rerun.proposal_digest,
            "base_commit": None,
            "head": head,
            "git_checked": False,
            "stale": False,
            "reasons": [],
        },
    ]


def test_staleness_collector_selects_requested_proposals(
    repository,
    tmp_path,
) -> None:
    root, base = repository
    store = JsonProposalStore(tmp_path / "proposals")
    store.save(deprovision_proposal(base))
    store.save(deprovision_proposal(base, "other", UUID(int=501)))

    snapshot = collect_staleness(
        root, tmp_path / "proposals", [UUID(int=501)]
    )

    assert [item.data["cluster_name"] for item in snapshot.items] == [
        "other"
    ]


def test_staleness_collector_reports_no_items_for_empty_store(
    repository,
    tmp_path,
) -> None:
    root, base = repository
    (tmp_path / "proposals").mkdir()

    snapshot = collect_staleness(root, tmp_path / "proposals")

    assert (snapshot.items, snapshot.source_version) == ([], base)


def test_staleness_collector_rejects_missing_store(
    repository,
    tmp_path,
) -> None:
    root, _ = repository

    with pytest.raises(
        ProposalRecordUnreadable,
        match="proposal store does not exist: .*missing",
    ):
        collect_staleness(root, tmp_path / "missing")


def test_staleness_collector_rejects_unreadable_store(
    repository,
    tmp_path,
) -> None:
    root, base = repository
    store = JsonProposalStore(tmp_path / "proposals")
    store.save(deprovision_proposal(base))
    store.root.chmod(0o000)
    try:
        with pytest.raises(
            ProposalRecordUnreadable,
            match="proposal store is unreadable: .*Permission denied",
        ):
            collect_staleness(root, store.root)
    finally:
        store.root.chmod(0o700)


def test_staleness_collector_checks_repeated_ids_once(
    repository,
    tmp_path,
) -> None:
    root, base = repository
    JsonProposalStore(tmp_path / "proposals").save(deprovision_proposal(base))

    snapshot = collect_staleness(
        root, tmp_path / "proposals", [UUID(int=500), UUID(int=500)]
    )

    assert len(snapshot.items) == 1


def test_staleness_collector_rejects_missing_requested_proposal(
    repository,
    tmp_path,
) -> None:
    root, _ = repository

    with pytest.raises(ProposalNotFound):
        collect_staleness(root, tmp_path / "proposals", [UUID(int=9)])


def _tamper(path: Path) -> None:
    path.chmod(0o600)
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "chore: deprovision example", "chore: deprovision example!"
        ),
        encoding="utf-8",
    )


def _replace_with_directory(path: Path) -> None:
    path.unlink()
    path.mkdir()


@pytest.mark.parametrize(
    ("damage", "message"),
    [
        (_tamper, f"proposal record is unreadable: {UUID(int=500)}.json"),
        (
            lambda path: path.chmod(0o000),
            f"proposal record is unreadable: {UUID(int=500)}.json",
        ),
        (
            _replace_with_directory,
            f"proposal record is unreadable: {UUID(int=500)}.json",
        ),
        (
            lambda path: (path.parent / "notes.json").write_text("{}"),
            "proposal store is unreadable: "
            "record name is not an ID: notes.json",
        ),
    ],
)
def test_staleness_collector_rejects_unreadable_records(
    repository,
    tmp_path,
    damage,
    message,
) -> None:
    root, base = repository
    path = JsonProposalStore(tmp_path / "proposals").save(
        deprovision_proposal(base)
    )
    damage(path)

    with pytest.raises(ProposalRecordUnreadable, match=message):
        collect_staleness(root, tmp_path / "proposals")
