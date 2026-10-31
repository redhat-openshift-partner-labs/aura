"""Typed contracts for Aura commands and evidence."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from aura.canonical import canonical_digest
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


ARCHIVE_SUFFIX = "-archive"
"""Suffix the fleet-clusters archive Action requires on deprovision sentinels."""


class CommandType(StrEnum):
    """Command types supported by the Aura control plane."""

    INVENTORY_LIST = "inventory.list"
    WORKFLOW_INSPECT = "workflow.inspect"
    DIAGNOSE_STATE = "diagnose.state"
    PROPOSE_PATCH = "propose.patch"
    PROPOSE_PIPELINE = "propose.pipeline"
    PROPOSAL_STATUS = "proposal.status"
    PROPOSAL_SUBMIT = "proposal.submit"
    PROPOSAL_APPROVE = "proposal.approve"
    PROPOSAL_REJECT = "proposal.reject"
    EXECUTE_PATCH = "execute.patch"
    EXECUTE_PIPELINE = "execute.pipeline"
    EXECUTION_STATUS = "execution.status"
    EXECUTION_CONFIRM = "execution.confirm"


class CommandState(StrEnum):
    """Lifecycle states for an Aura command."""

    RECEIVED = "received"
    COLLECTING_EVIDENCE = "collecting_evidence"
    ASSEMBLING_CONTEXT = "assembling_context"
    MODEL_CALLED = "model_called"
    PROPOSAL_READY = "proposal_ready"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    REFUSED = "refused"
    EXECUTING = "executing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


def reject_blank_entries(values: list[str], label: str) -> list[str]:
    """Return values unchanged, rejecting any blank entry."""

    if any(not entry.strip() for entry in values):
        raise ValueError(f"{label} must not be blank")
    return values


def _resolve_digest(
    digest: str,
    computed_digest: str,
    mismatch_message: str,
) -> str:
    if not digest:
        return computed_digest
    if digest != computed_digest:
        raise ValueError(mismatch_message)
    return digest


def assign_self_digest(record: BaseModel, field_name: str, label: str) -> None:
    """Compute a record's digest over its other fields, assign it when
    blank, and reject a stored value that differs."""

    _assign_digest(
        record,
        field_name,
        canonical_digest(
            record.model_dump(mode="json", exclude={field_name})
        ),
        f"{label} digest does not match content",
    )


def _assign_digest(
    record: BaseModel,
    field_name: str,
    computed_digest: str,
    mismatch_message: str,
) -> None:
    setattr(
        record,
        field_name,
        _resolve_digest(
            getattr(record, field_name),
            computed_digest,
            mismatch_message,
        ),
    )


class CommandEnvelope(BaseModel):
    """Envelope for an operator-issued Aura command."""

    model_config = ConfigDict(extra="forbid")

    command_id: UUID
    command_type: CommandType
    operator_id: str = Field(min_length=1)
    created_at: AwareDatetime
    input: dict[str, Any]
    idempotency_key: str = Field(min_length=1)
    status: CommandState = CommandState.RECEIVED
    request_digest: str = Field(default="")
    record_digest: str = Field(default="")

    @field_validator("operator_id", "idempotency_key")
    @classmethod
    def reject_blank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value

    def with_status(self, status: CommandState) -> "CommandEnvelope":
        """Return a validated copy with a new lifecycle status."""

        values = self.model_dump(mode="json")
        values["status"] = status.value
        values["record_digest"] = ""
        return CommandEnvelope.model_validate(values)

    @model_validator(mode="after")
    def validate_command_digests(self) -> "CommandEnvelope":
        _assign_digest(
            self,
            "request_digest",
            compute_command_request_digest(self),
            "command request digest does not match request",
        )
        _assign_digest(
            self,
            "record_digest",
            compute_command_record_digest(self),
            "command record digest does not match record",
        )
        return self


def _canonical_digest(payload: dict[str, Any]) -> str:
    return canonical_digest(payload)


def compute_command_request_digest(command: CommandEnvelope) -> str:
    """Return a digest identifying the operator request, not its execution."""

    return _canonical_digest(
        {
            "command_type": command.command_type.value,
            "operator_id": command.operator_id,
            "input": command.input,
        }
    )


def compute_command_record_digest(command: CommandEnvelope) -> str:
    """Return a canonical SHA-256 digest for a command record."""

    return _canonical_digest(
        command.model_dump(mode="json", exclude={"record_digest"})
    )


class EvidenceItem(BaseModel):
    """A single typed observation produced by an evidence collector."""

    model_config = ConfigDict(extra="forbid")

    item_id: UUID
    kind: str = Field(min_length=1)
    data: dict[str, Any]

    @field_validator("kind")
    @classmethod
    def reject_blank_kind(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value


class EvidenceSnapshot(BaseModel):
    """Frozen output from one deterministic evidence collector."""

    model_config = ConfigDict(extra="forbid")

    snapshot_id: UUID
    command_id: UUID
    collector: str = Field(min_length=1)
    collector_version: str = Field(min_length=1)
    collected_at: AwareDatetime
    source: str = Field(min_length=1)
    source_version: str = Field(min_length=1)
    items: list[EvidenceItem] = Field(default_factory=list)
    redactions: list[str] = Field(default_factory=list)
    snapshot_digest: str = Field(default="")

    @field_validator("collector", "collector_version", "source", "source_version")
    @classmethod
    def reject_blank_snapshot_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value

    @field_validator("redactions")
    @classmethod
    def reject_blank_redactions(cls, value: list[str]) -> list[str]:
        return reject_blank_entries(value, "redaction entries")

    @model_validator(mode="after")
    def validate_snapshot_digest(self) -> "EvidenceSnapshot":
        _assign_digest(
            self,
            "snapshot_digest",
            compute_snapshot_digest(self),
            "snapshot digest does not match snapshot content",
        )
        return self


def compute_snapshot_digest(snapshot: EvidenceSnapshot) -> str:
    """Return a canonical SHA-256 digest for an evidence snapshot."""

    payload = snapshot.model_dump(mode="json", exclude={"snapshot_digest"})
    return canonical_digest(payload)


class AuditEventType(StrEnum):
    """Event types recorded in the Aura audit trail."""

    COMMAND_RECEIVED = "command_received"
    EVIDENCE_COLLECTED = "evidence_collected"
    CONTEXT_ASSEMBLED = "context_assembled"
    MODEL_CALLED = "model_called"
    PROPOSAL_CREATED = "proposal_created"
    PROPOSAL_PUBLISHED = "proposal_published"
    APPROVAL_RECORDED = "approval_recorded"
    EXECUTION_STARTED = "execution_started"
    EXECUTION_FINISHED = "execution_finished"
    EXECUTION_OBSERVED = "execution_observed"
    RESOLUTION_CONFIRMED = "resolution_confirmed"
    COMMAND_REJECTED = "command_rejected"
    COMMAND_FAILED = "command_failed"
    COMMAND_REFUSED = "command_refused"


class AuditEvent(BaseModel):
    """An append-only record for an Aura action."""

    model_config = ConfigDict(extra="forbid")

    event_id: UUID
    command_id: UUID
    event_type: AuditEventType
    occurred_at: AwareDatetime
    actor: str = Field(min_length=1)
    data: dict[str, Any]
    previous_event_id: UUID | None = None
    event_digest: str = Field(default="")

    @field_validator("actor")
    @classmethod
    def reject_blank_actor(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value

    @model_validator(mode="after")
    def validate_event_digest(self) -> "AuditEvent":
        _assign_digest(
            self,
            "event_digest",
            compute_audit_event_digest(self),
            "audit event digest does not match event",
        )
        return self


def compute_audit_event_digest(event: AuditEvent) -> str:
    """Return a canonical SHA-256 digest for an audit event."""

    return _canonical_digest(
        event.model_dump(mode="json", exclude={"event_digest"})
    )


class PatchCheckOutcome(StrEnum):
    """How a Git proposal's patch fared against its base commit."""

    APPLIES = "applies"
    COMPLETE = "complete"
    REFUSED = "refused"


class PatchCheckRecord(BaseModel):
    """The outcome of checking a Git patch against fleet-clusters.

    The check runs during generation, after the evidence is frozen, so
    the outcome is recorded on the proposal and its CWA trace.
    """

    model_config = ConfigDict(extra="forbid")

    base_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    cluster_name: str = Field(min_length=1)
    patch_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    require_definition: bool
    outcome: PatchCheckOutcome
    reasons: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_outcome(self) -> "PatchCheckRecord":
        refused = self.outcome is PatchCheckOutcome.REFUSED
        if refused != bool(self.reasons):
            raise ValueError("only a refused patch check has reasons")
        reject_blank_entries(self.reasons, "reasons")
        passed = (
            PatchCheckOutcome.COMPLETE
            if self.require_definition
            else PatchCheckOutcome.APPLIES
        )
        if not refused and self.outcome is not passed:
            raise ValueError(
                f"a passed patch check that requires a definition is "
                f"{PatchCheckOutcome.COMPLETE.value}, otherwise "
                f"{PatchCheckOutcome.APPLIES.value}"
            )
        return self


__all__ = [
    "ARCHIVE_SUFFIX",
    "AuditEvent",
    "AuditEventType",
    "CommandEnvelope",
    "CommandState",
    "CommandType",
    "EvidenceItem",
    "EvidenceSnapshot",
    "PatchCheckOutcome",
    "PatchCheckRecord",
    "compute_audit_event_digest",
    "compute_command_record_digest",
    "compute_command_request_digest",
    "compute_snapshot_digest",
    "reject_blank_entries",
]

