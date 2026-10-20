"""Read-only staleness check for proposals against fleet-clusters HEAD."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import final

from aura.collectors.git_inventory import GitInventoryError, read_git
from aura.contracts import ARCHIVE_SUFFIX
from aura.proposals import Proposal


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

    Post-provision reruns have no base commit; their evidence is rechecked
    when the proposal is generated or executed.
    """

    def __init__(self, root: Path) -> None:
        self.root = root

    def check(self, proposal: Proposal) -> ProposalStaleness:
        head = read_git(self.root, ["rev-parse", "HEAD"]).strip()
        base = proposal.base_commit
        if base is None or base == head:
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
                    "-C",
                    str(self.root),
                    "merge-base",
                    "--is-ancestor",
                    base,
                    head,
                ],
                capture_output=True,
                text=True,
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


__all__ = ["GitProposalStaleness", "ProposalStaleness", "cluster_paths"]
