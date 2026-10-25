from __future__ import annotations

import json
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

    def _parse(self, value: str) -> Proposal:
        """Require the stored digest, which validation would recompute."""

        document = json.loads(value)
        digest = (
            document.get("proposal_digest")
            if isinstance(document, dict)
            else None
        )
        if not isinstance(digest, str) or not digest:
            raise ValueError("proposal record has no stored proposal_digest")
        return Proposal.model_validate(document)


__all__ = [
    "JsonProposalStore",
    "ProposalAlreadyStored",
    "ProposalNotFound",
]
