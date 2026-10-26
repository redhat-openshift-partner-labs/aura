from __future__ import annotations

from typing import final

from aura.json_store import JsonRecordStore
from aura.proposals import Proposal


class ProposalAlreadyStored(Exception):
    """Raised when saving a proposal that already exists."""


class ProposalNotFound(Exception):
    """Raised when loading a proposal that does not exist."""


@final
class JsonProposalStore(JsonRecordStore[Proposal]):
    """Stores immutable proposals as JSON files keyed by proposal ID."""

    record_type = Proposal
    record_label = "proposal"
    id_field = "proposal_id"
    already_stored = ProposalAlreadyStored
    not_found = ProposalNotFound


__all__ = [
    "JsonProposalStore",
    "ProposalAlreadyStored",
    "ProposalNotFound",
]
