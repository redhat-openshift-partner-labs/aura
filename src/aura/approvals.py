"""Records of Phase 3 proposal publication and approval (task 2.2)."""

from __future__ import annotations

from datetime import timedelta
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid5

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from aura.canonical import canonical_json
from aura.contracts import (
    AuditEvent,
    AuditEventType,
    CommandEnvelope,
    CommandState,
    assign_self_digest,
)
from aura.cwa import CWATrace
from aura.proposals import Proposal


APPROVAL_LIFETIME = timedelta(hours=24)
_PUBLICATION_NAMESPACE = UUID("4a1d7a9e-6c2b-4f0e-9d3a-7b5e2c8f1a60")
_DIGEST = r"^[0-9a-f]{64}$"
_SSH_SIGNATURE = (
    r"^-----BEGIN SSH SIGNATURE-----\n[A-Za-z0-9+/=\n]+\n"
    r"-----END SSH SIGNATURE-----\n?$"
)
# Stored digests each nested record must carry, so none is recomputed.
_NESTED_DIGESTS = {
    "proposal": ("proposal_digest",),
    "cwa_trace": ("trace_digest",),
    "proposal_created": ("event_digest",),
    "command": ("request_digest", "record_digest"),
}


def publication_id_for(proposal_id: UUID) -> UUID:
    """Derive a publication's ID, so a republish reads back the same row."""

    return uuid5(_PUBLICATION_NAMESPACE, str(proposal_id))


class PublishedProposal(BaseModel):
    """A Phase 2 proposal with the records that prove its provenance,
    published to the execution store for approvers and executors."""

    model_config = ConfigDict(extra="forbid")

    publication_id: UUID
    proposal_id: UUID
    proposal_digest: str = Field(pattern=_DIGEST)
    proposal: Proposal
    cwa_trace: CWATrace
    proposal_created: AuditEvent
    command: CommandEnvelope
    created_at: AwareDatetime
    publication_digest: str = Field(default="")

    @model_validator(mode="before")
    @classmethod
    def require_nested_digests(cls, values: Any) -> Any:
        if isinstance(values, dict):
            for name, fields in _NESTED_DIGESTS.items():
                nested = values.get(name)
                for field in fields if isinstance(nested, dict) else ():
                    if not nested.get(field):
                        raise ValueError(f"{name} has no stored {field}")
        return values

    @model_validator(mode="after")
    def validate_bundle(self) -> "PublishedProposal":
        proposal = self.proposal
        trace = self.cwa_trace
        event = self.proposal_created
        expected = {
            "publication_id": (
                self.publication_id,
                publication_id_for(proposal.proposal_id),
            ),
            "proposal_id": (self.proposal_id, proposal.proposal_id),
            "proposal_digest": (self.proposal_digest, proposal.proposal_digest),
            "cwa_trace trace_id": (trace.trace_id, proposal.cwa_trace_id),
            "cwa_trace command_id": (trace.command_id, proposal.command_id),
            "cwa_trace response_digest": (
                trace.response_digest,
                proposal.proposal_digest,
            ),
            "proposal_created event_type": (
                event.event_type,
                AuditEventType.PROPOSAL_CREATED,
            ),
            "proposal_created command_id": (
                event.command_id,
                proposal.command_id,
            ),
            "proposal_created proposal_id": (
                event.data.get("proposal_id"),
                str(proposal.proposal_id),
            ),
            "proposal_created proposal_digest": (
                event.data.get("proposal_digest"),
                proposal.proposal_digest,
            ),
            "command command_id": (self.command.command_id, proposal.command_id),
            "command status": (self.command.status, CommandState.SUCCEEDED),
        }
        for name, (actual, wanted) in expected.items():
            if actual != wanted:
                raise ValueError(f"{name} does not match the proposal")
        assign_self_digest(self, "publication_digest", "published proposal")
        return self


class ApprovalDecision(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"


class ApprovalRecord(BaseModel):
    """A signed approval or rejection of one proposal's action (D7, D11)."""

    model_config = ConfigDict(extra="forbid")

    approval_id: UUID
    command_id: UUID
    proposal_id: UUID
    proposal_digest: str = Field(pattern=_DIGEST)
    action_digest: str = Field(pattern=_DIGEST)
    decision: ApprovalDecision
    principal: str = Field(min_length=1)
    signature: str = Field(pattern=_SSH_SIGNATURE)
    created_at: AwareDatetime
    expires_at: AwareDatetime | None = None
    audit_event_digest: str = Field(pattern=_DIGEST)
    approval_digest: str = Field(default="")

    @field_validator("principal")
    @classmethod
    def reject_blank_principal(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value

    @model_validator(mode="after")
    def validate_approval(self) -> "ApprovalRecord":
        if self.decision is ApprovalDecision.REJECTED:
            if self.expires_at is not None:
                raise ValueError("a rejection has no expires_at")
        elif self.expires_at is None:
            raise ValueError("an approval requires expires_at")
        elif not (
            self.created_at
            < self.expires_at
            <= self.created_at + APPROVAL_LIFETIME
        ):
            raise ValueError(
                "expires_at must be after created_at and at most 24 hours "
                "later"
            )
        assign_self_digest(self, "approval_digest", "approval")
        return self

    def signed_payload(self) -> bytes:
        """Return what the signer signs: every field but the signature and
        the record's own digest."""

        return canonical_json(
            self.model_dump(
                mode="json",
                exclude={"signature", "approval_digest"},
            )
        ).encode("utf-8")


__all__ = [
    "APPROVAL_LIFETIME",
    "ApprovalDecision",
    "ApprovalRecord",
    "PublishedProposal",
    "publication_id_for",
]
