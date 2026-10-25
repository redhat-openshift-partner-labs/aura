"""Read-only staleness check for proposals against fleet-clusters HEAD."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import final
from uuid import UUID, uuid5

from aura.collectors.git_inventory import GitInventoryError, read_git
from aura.contracts import ARCHIVE_SUFFIX, EvidenceItem, EvidenceSnapshot
from aura.git_isolation import GIT_ISOLATION, isolated_git_environment
from aura.proposal_store import JsonProposalStore
from aura.proposals import Proposal


class ProposalRecordUnreadable(Exception):
    """Raised when a stored proposal record is malformed or tampered."""


@dataclass(frozen=True)
class ProposalStaleness:
    stale: bool
    head: str
    reasons: tuple[str, ...]


def cluster_paths(cluster_name: str) -> list[str]:
    """Return every fleet-clusters path that belongs to one cluster."""

    return [
        f"provision/{cluster_name}",
        f"archive/{cluster_name}",
        f"deprovision/{cluster_name}",
        f"deprovision/{cluster_name}{ARCHIVE_SUFFIX}",
    ]


@final
class GitProposalStaleness:
    """Reports whether HEAD changed a proposal's cluster since its base commit.

    Every proposal type carries a base commit; for a rerun it is the
    revision whose metadata.yaml and definition the evidence read. Live
    state is rechecked by the Phase 3 execution guard, not here.
    """

    def __init__(self, root: Path) -> None:
        self.root = root

    def head(self) -> str:
        return read_git(self.root, ["rev-parse", "HEAD"]).strip()

    def check(
        self,
        proposal: Proposal,
        head: str | None = None,
    ) -> ProposalStaleness:
        head = head or self.head()
        base = proposal.base_commit
        if base is None:
            return ProposalStaleness(
                stale=True, head=head, reasons=("proposal has no base commit",)
            )
        if base == head:
            return ProposalStaleness(stale=False, head=head, reasons=())
        history_error = self._history_error(base, head)
        if history_error is not None:
            return ProposalStaleness(
                stale=True, head=head, reasons=(history_error,)
            )
        changed = read_git(
            self.root,
            [
                "diff",
                "--name-only",
                "-z",
                base,
                head,
                "--",
                *cluster_paths(proposal.cluster_name),
            ],
        )
        reasons = tuple(
            f"{path} changed since base commit {base}"
            for path in sorted(filter(None, changed.split("\0")))
        )
        return ProposalStaleness(stale=bool(reasons), head=head, reasons=reasons)

    def _history_error(self, base: str, head: str) -> str | None:
        try:
            result = subprocess.run(
                [
                    "git",
                    *GIT_ISOLATION,
                    "-C",
                    str(self.root),
                    "merge-base",
                    "--is-ancestor",
                    base,
                    head,
                ],
                capture_output=True,
                stdin=subprocess.DEVNULL,
                text=True,
                env=isolated_git_environment(),
            )
        except OSError as error:
            raise GitInventoryError(
                f"git checkout is not usable: {self.root}"
            ) from error
        if result.returncode == 0:
            return None
        if result.returncode == 1:
            return f"base commit {base} is not an ancestor of HEAD {head}"
        return f"base commit {base} is not in the checkout history"


@final
class ProposalStalenessCollector:
    """Reports Git staleness of stored proposals against one pinned HEAD."""

    collector_version = "0.1.0"

    def __init__(
        self,
        *,
        proposal_store: JsonProposalStore,
        staleness: GitProposalStaleness,
        proposal_ids: list[UUID] | None = None,
    ) -> None:
        self._store = proposal_store
        self._staleness = staleness
        self._proposal_ids = proposal_ids

    def collect(
        self,
        *,
        command_id: UUID,
        snapshot_id: UUID | None = None,
        collected_at: datetime | None = None,
        source_version: str | None = None,
    ) -> EvidenceSnapshot:
        head = self._staleness.head()
        items = [
            _staleness_item(
                command_id,
                proposal,
                self._staleness.check(proposal, head),
            )
            for proposal in self._proposals()
        ]
        return EvidenceSnapshot(
            snapshot_id=snapshot_id or uuid5(command_id, "proposal-staleness"),
            command_id=command_id,
            collector="proposal_staleness",
            collector_version=self.collector_version,
            collected_at=collected_at or datetime.now(UTC),
            source="fleet-clusters",
            source_version=source_version or head,
            items=items,
            redactions=[],
        )

    def _proposals(self) -> list[Proposal]:
        try:
            proposal_ids = self._proposal_ids or self._store.record_ids()
        except FileNotFoundError as error:
            raise ProposalRecordUnreadable(
                f"proposal store does not exist: {self._store.root}"
            ) from error
        except (OSError, ValueError) as error:
            raise ProposalRecordUnreadable(
                f"proposal store is unreadable: {error}"
            ) from error
        return [
            self._load(proposal_id)
            for proposal_id in dict.fromkeys(proposal_ids)
        ]

    def _load(self, proposal_id: UUID) -> Proposal:
        try:
            return self._store.load(proposal_id)
        except (OSError, ValueError) as error:
            raise ProposalRecordUnreadable(
                f"proposal record is unreadable: {proposal_id}.json"
            ) from error


def _staleness_item(
    command_id: UUID,
    proposal: Proposal,
    staleness: ProposalStaleness,
) -> EvidenceItem:
    return EvidenceItem(
        item_id=uuid5(
            command_id,
            f"proposal-staleness:{proposal.proposal_id}",
        ),
        kind="proposal_staleness",
        data={
            "proposal_id": str(proposal.proposal_id),
            "proposal_type": proposal.proposal_type.value,
            "cluster_name": proposal.cluster_name,
            "proposal_digest": proposal.proposal_digest,
            "base_commit": proposal.base_commit,
            "head": staleness.head,
            "git_checked": proposal.base_commit is not None,
            "stale": staleness.stale,
            "reasons": list(staleness.reasons),
        },
    )


__all__ = [
    "GitProposalStaleness",
    "ProposalRecordUnreadable",
    "ProposalStaleness",
    "ProposalStalenessCollector",
    "cluster_paths",
]
