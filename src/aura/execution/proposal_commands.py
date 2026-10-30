"""`proposal submit`, `proposal approve`, and `proposal reject` (task 2.4)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import UUID, uuid5

from pydantic import ValidationError

from aura.approvals import PublishedProposal, publication_id_for
from aura.audit_store import AuditEventNotFound, JsonAuditEventStore
from aura.command_store import (
    CommandNotFound,
    CommandRecordUnreadable,
    find_command,
)
from aura.contracts import AuditEventType
from aura.execution.commands import Chain, Refusal
from aura.provenance import ProvenanceError, load_proposal_provenance


@dataclass(frozen=True)
class SubmitSources:
    """The Phase 2 JSON stores that hold the proposal to publish."""

    proposal_dir: Path
    cwa_dir: Path
    snapshot_dir: Path
    audit_dir: Path
    command_dir: Path


def submit_step(
    proposal_id: UUID,
    sources: SubmitSources,
    clock: Callable[[], datetime],
) -> Callable[[Chain], str]:
    def step(chain: Chain) -> str:
        published = _published(proposal_id, sources, clock())
        stored = chain.store.select("published_proposals", proposal_id=proposal_id)
        if stored:
            raise Refusal(
                f"proposal {proposal_id} is already published as "
                f"{stored[0].publication_id}"
            )
        chain.append(
            AuditEventType.PROPOSAL_PUBLISHED,
            {
                "publication_id": str(published.publication_id),
                "proposal_id": str(proposal_id),
                "proposal_digest": published.proposal_digest,
                "publication_digest": published.publication_digest,
            },
            subject_id=published.publication_id,
        )
        chain.store_artifact("published_proposals", published)
        return published.model_dump_json(indent=2)

    return step


def _published(
    proposal_id: UUID,
    sources: SubmitSources,
    created_at: datetime,
) -> PublishedProposal:
    """Load the proposal with its provenance (G3) and its command record,
    which must be succeeded (G4); refuse when any of them fails."""

    try:
        provenance = load_proposal_provenance(
            proposal_id,
            proposal_dir=sources.proposal_dir,
            cwa_dir=sources.cwa_dir,
            snapshot_dir=sources.snapshot_dir,
            audit_dir=sources.audit_dir,
        )
    except ProvenanceError as error:
        raise Refusal(f"proposal provenance is invalid: {error}") from error
    proposal = provenance.proposal
    try:
        command = find_command(sources.command_dir, proposal.command_id)
        created = JsonAuditEventStore(sources.audit_dir).load(
            uuid5(
                proposal.command_id,
                f"{AuditEventType.PROPOSAL_CREATED.value}:{proposal_id}",
            )
        )
    except (CommandNotFound, CommandRecordUnreadable, AuditEventNotFound) as error:
        raise Refusal(f"proposal provenance is invalid: {error}") from error
    try:
        return PublishedProposal(
            publication_id=publication_id_for(proposal_id),
            proposal_id=proposal_id,
            proposal_digest=proposal.proposal_digest,
            proposal=proposal,
            cwa_trace=provenance.trace,
            proposal_created=created,
            command=command,
            created_at=created_at,
        )
    except ValidationError as error:
        raise Refusal(f"proposal cannot be published: {error}") from error


__all__ = ["SubmitSources", "submit_step"]
