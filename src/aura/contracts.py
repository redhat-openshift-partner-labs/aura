"""Typed contracts for Aura commands and evidence."""

from __future__ import annotations

import json
from datetime import datetime
from enum import StrEnum
from hashlib import sha256
from typing import Any
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


class CommandType(StrEnum):
    """Command types supported by the Aura control plane."""

    INVENTORY_LIST = "inventory.list"
    WORKFLOW_INSPECT = "workflow.inspect"
    DIAGNOSE_STATE = "diagnose.state"
    PROPOSE_PATCH = "propose.patch"
    PROPOSE_PIPELINE = "propose.pipeline"
    EXECUTE_PATCH = "execute.patch"
    EXECUTE_PIPELINE = "execute.pipeline"


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
    EXECUTING = "executing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    AWAITING_RETRY = "awaiting_retry"


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

    @field_validator("operator_id", "idempotency_key")
    @classmethod
    def reject_blank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value


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
        if any(not entry.strip() for entry in value):
            raise ValueError("redaction entries must not be blank")
        return value

    @model_validator(mode="after")
    def validate_snapshot_digest(self) -> "EvidenceSnapshot":
        computed_digest = compute_snapshot_digest(self)
        if not self.snapshot_digest:
            self.snapshot_digest = computed_digest
        elif self.snapshot_digest != computed_digest:
            raise ValueError("snapshot digest does not match snapshot content")
        return self


def compute_snapshot_digest(snapshot: EvidenceSnapshot) -> str:
    """Return a canonical SHA-256 digest for an evidence snapshot."""

    payload = snapshot.model_dump(mode="json", exclude={"snapshot_digest"})
    canonical_json = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return sha256(canonical_json.encode("utf-8")).hexdigest()


class AuditEventType(StrEnum):
    """Event types recorded in the Aura audit trail."""

    COMMAND_RECEIVED = "command_received"
    EVIDENCE_COLLECTED = "evidence_collected"
    CONTEXT_ASSEMBLED = "context_assembled"
    MODEL_CALLED = "model_called"
    PROPOSAL_CREATED = "proposal_created"
    APPROVAL_RECORDED = "approval_recorded"
    EXECUTION_STARTED = "execution_started"
    EXECUTION_FINISHED = "execution_finished"
    COMMAND_REJECTED = "command_rejected"


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

    @field_validator("actor")
    @classmethod
    def reject_blank_actor(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value


__all__ = [
    "AuditEvent",
    "AuditEventType",
    "CommandEnvelope",
    "CommandState",
    "CommandType",
    "EvidenceItem",
    "EvidenceSnapshot",
    "compute_snapshot_digest",
]

