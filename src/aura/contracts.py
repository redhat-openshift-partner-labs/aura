"""Typed contracts for Aura commands and evidence."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator


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


__all__ = [
    "CommandEnvelope",
    "CommandState",
    "CommandType",
    "EvidenceItem",
    "EvidenceSnapshot",
]

