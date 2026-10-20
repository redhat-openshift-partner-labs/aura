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


class CWASlotName(StrEnum):
    """Names of the required slots in a Context Window Architecture snapshot."""

    GOVERNANCE_INSTRUCTIONS = "governance.instructions"
    GOVERNANCE_CAPABILITIES = "governance.capabilities"
    GOVERNANCE_OUTPUT_CONTRACT = "governance.output_contract"
    STATE_TASK = "state.task"
    STATE_USER = "state.user"
    EVIDENCE_TOOL_RESULTS = "evidence.tool_results"
    EVIDENCE_KNOWLEDGE = "evidence.knowledge"
    INTERACTION_QUERY = "interaction.query"
    INTERACTION_HISTORY = "interaction.history"


class CWASlot(BaseModel):
    """One named slot in a CWA snapshot."""

    model_config = ConfigDict(extra="forbid")

    name: CWASlotName
    content: dict[str, Any]


class CWASnapshot(BaseModel):
    """Frozen input assembled for a model call."""

    model_config = ConfigDict(extra="forbid")

    snapshot_id: UUID
    command_id: UUID
    evidence_snapshot_id: UUID
    evidence_snapshot_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    assembler_version: str = Field(min_length=1)
    assembled_at: AwareDatetime
    slots: list[CWASlot]
    snapshot_digest: str = Field(default="")

    @field_validator("assembler_version")
    @classmethod
    def reject_blank_assembler_version(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value

    @model_validator(mode="after")
    def normalize_slots_and_validate_digest(self) -> "CWASnapshot":
        names = [slot.name for slot in self.slots]
        if len(names) != len(set(names)):
            raise ValueError("CWA snapshot contains duplicate slots")

        required_names = set(CWASlotName)
        missing_names = required_names.difference(names)
        if missing_names:
            missing = ", ".join(
                sorted(name.value for name in missing_names)
            )
            raise ValueError(f"CWA snapshot is missing slots: {missing}")

        self.slots.sort(key=lambda slot: slot.name.value)
        computed_digest = compute_cwa_snapshot_digest(self)
        if not self.snapshot_digest:
            self.snapshot_digest = computed_digest
        elif self.snapshot_digest != computed_digest:
            raise ValueError("CWA snapshot digest does not match content")
        return self


class CWATrace(BaseModel):
    """Trace record linking a CWA snapshot to evidence and audit state."""

    model_config = ConfigDict(extra="forbid")

    trace_id: UUID
    command_id: UUID
    cwa_snapshot_id: UUID
    cwa_snapshot_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_snapshot_id: UUID
    evidence_snapshot_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    audit_event_id: UUID
    assembler_version: str = Field(min_length=1)
    assembled_at: AwareDatetime
    model_provider: str | None = Field(default=None, min_length=1)
    model_name: str | None = Field(default=None, min_length=1)
    response_digest: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )

    @field_validator(
        "assembler_version",
        "model_provider",
        "model_name",
    )
    @classmethod
    def reject_blank_text(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("must not be blank")
        return value


def compute_cwa_snapshot_digest(snapshot: CWASnapshot) -> str:
    """Return a canonical SHA-256 digest for a CWA snapshot."""

    payload = snapshot.model_dump(mode="json", exclude={"snapshot_digest"})
    return canonical_digest(payload)


__all__ = [
    "CWASlot",
    "CWASlotName",
    "CWASnapshot",
    "CWATrace",
    "compute_cwa_snapshot_digest",
]
