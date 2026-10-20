from __future__ import annotations

from pathlib import Path
from typing import final
from uuid import UUID

from aura.json_store import save_private_json
from aura.proposals import Proposal


class ProposalAlreadyStored(Exception):
    """Raised when saving a proposal that already exists."""


class ProposalNotFound(Exception):
    """Raised when loading a proposal that does not exist."""


@final
class JsonProposalStore:
    """Stores immutable proposals as JSON files keyed by proposal ID."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def save(self, proposal: Proposal) -> Path:
        return save_private_json(
            self.root,
            self._path(proposal.proposal_id),
            proposal,
            lambda message: ProposalAlreadyStored(message),
            f"proposal already stored: {proposal.proposal_id}"
        )

    def load(self, proposal_id: UUID) -> Proposal:
        path = self._path(proposal_id)
        try:
            value = path.read_text(encoding="utf-8")
        except FileNotFoundError as error:
            raise ProposalNotFound(
                f"proposal not found: {proposal_id}"
            ) from error
        return Proposal.model_validate_json(value)

    def _path(self, proposal_id: UUID) -> Path:
        return self.root / f"{proposal_id}.json"


__all__ = [
    "JsonProposalStore",
    "ProposalAlreadyStored",
    "ProposalNotFound",
]
