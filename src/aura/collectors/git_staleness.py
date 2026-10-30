"""Read-only staleness check for proposals against fleet-clusters HEAD."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar, final
from uuid import UUID, uuid5

from aura.collectors.git_inventory import GitInventoryError, read_git
from aura.contracts import ARCHIVE_SUFFIX, EvidenceItem, EvidenceSnapshot
from aura.git_isolation import (
    GIT_ISOLATION,
    GIT_TIMEOUT,
    isolated_git_environment,
)
from aura.process import ProcessError, run_process
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
            result = run_process(
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
                env=isolated_git_environment(),
                timeout=GIT_TIMEOUT,
            )
        except ProcessError as error:
            raise GitInventoryError(
                f"git command failed for fleet-clusters: {error}"
                if error.timed_out
                else f"git checkout is not usable: {self.root}"
            ) from error
        if result.returncode == 0:
            return None
        if result.returncode == 1:
            return f"base commit {base} is not an ancestor of HEAD {head}"
        return f"base commit {base} is not in the checkout history"


@final
@dataclass(frozen=True, kw_only=True)
class ProposalStalenessCollector:
    """Reports Git staleness of stored proposals against one pinned HEAD."""

    collector_version: ClassVar[str] = "0.1.0"

    proposal_store: JsonProposalStore
    staleness: GitProposalStaleness
    proposal_ids: list[UUID] | None = None
    # Phase 3: returns whether a proposal is published and its derived
    # state; without it both are reported as not read.
    derive: Callable[[Proposal, bool], tuple[bool, str]] | None = None

    def collect(
        self,
        *,
        command_id: UUID,
        snapshot_id: UUID | None = None,
        collected_at: datetime | None = None,
        source_version: str | None = None,
    ) -> EvidenceSnapshot:
        head = self.staleness.head()
        items = []
        for proposal in self._proposals():
            staleness = self.staleness.check(proposal, head)
            derived = (
                None
                if self.derive is None
                else self.derive(proposal, staleness.stale)
            )
            items.append(_staleness_item(command_id, proposal, staleness, derived))
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
            proposal_ids = self.proposal_ids or self.proposal_store.record_ids()
        except FileNotFoundError as error:
            raise ProposalRecordUnreadable(
                f"proposal store does not exist: {self.proposal_store.root}"
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
            return self.proposal_store.load(proposal_id)
        except (OSError, ValueError) as error:
            raise ProposalRecordUnreadable(
                f"proposal record is unreadable: {proposal_id}.json"
            ) from error


def _staleness_item(
    command_id: UUID,
    proposal: Proposal,
    staleness: ProposalStaleness,
    derived: tuple[bool, str] | None,
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
            "published": None if derived is None else derived[0],
            "derived_state": None if derived is None else derived[1],
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
