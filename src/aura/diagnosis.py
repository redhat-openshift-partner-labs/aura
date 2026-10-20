from __future__ import annotations

from typing import Protocol
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from aura.canonical import canonical_digest
from aura.cwa import CWASnapshot


class DiagnosisFact(BaseModel):
    """A deterministic observation used by a diagnosis."""

    model_config = ConfigDict(extra="forbid")

    evidence_id: UUID
    observation: str = Field(min_length=1)

    @field_validator("observation")
    @classmethod
    def reject_blank_observation(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value


class DiagnosisInference(BaseModel):
    """A model-generated explanation marked as inference."""

    model_config = ConfigDict(extra="forbid")

    statement: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)

    @field_validator("statement")
    @classmethod
    def reject_blank_statement(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value


class RecommendedInspection(BaseModel):
    """A read-only next inspection step."""

    model_config = ConfigDict(extra="forbid")

    description: str = Field(min_length=1)

    @field_validator("description")
    @classmethod
    def reject_blank_description(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value


class DiagnosisResponse(BaseModel):
    """Strict JSON response contract for Phase 1 diagnoses."""

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1)
    facts: list[DiagnosisFact] = Field(default_factory=list)
    inferences: list[DiagnosisInference] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    recommended_inspection: RecommendedInspection
    confidence: float = Field(ge=0, le=1)
    evidence_ids: list[UUID] = Field(default_factory=list)

    @field_validator("summary")
    @classmethod
    def reject_blank_summary(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value

    @field_validator("missing_evidence")
    @classmethod
    def reject_blank_missing_evidence(
        cls,
        value: list[str],
    ) -> list[str]:
        if any(not entry.strip() for entry in value):
            raise ValueError("missing evidence entries must not be blank")
        return value

    @model_validator(mode="after")
    def validate_evidence_references(self) -> DiagnosisResponse:
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("evidence IDs must be unique")
        fact_evidence_ids = {fact.evidence_id for fact in self.facts}
        if not fact_evidence_ids.issubset(set(self.evidence_ids)):
            raise ValueError("every fact must reference an evidence ID")
        return self


class DiagnosisModelUnavailable(Exception):
    """Raised when a diagnosis model cannot return a response."""


class DiagnosisEvidenceUnavailable(Exception):
    """Raised when required evidence is unavailable for a diagnosis."""

    def __init__(self, missing_evidence: list[str]) -> None:
        if not missing_evidence:
            raise ValueError("missing evidence must not be empty")
        if any(not entry.strip() for entry in missing_evidence):
            raise ValueError("missing evidence entries must not be blank")
        self.missing_evidence = missing_evidence
        super().__init__(
            "missing required evidence: "
            + ", ".join(missing_evidence)
        )


class DiagnosisRecord(BaseModel):
    """Immutable diagnosis response linked to its CWA input."""

    model_config = ConfigDict(extra="forbid")

    diagnosis_id: UUID
    command_id: UUID
    cwa_snapshot_id: UUID
    cwa_snapshot_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider_name: str = Field(min_length=1)
    model_name: str = Field(min_length=1)
    used_fallback: bool
    diagnosed_at: AwareDatetime
    response: DiagnosisResponse
    response_digest: str = Field(default="")

    @field_validator("provider_name", "model_name")
    @classmethod
    def reject_blank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value

    @model_validator(mode="after")
    def validate_response_digest(self) -> "DiagnosisRecord":
        computed_digest = compute_diagnosis_response_digest(self.response)
        if not self.response_digest:
            self.response_digest = computed_digest
        elif self.response_digest != computed_digest:
            raise ValueError("response digest does not match response")
        return self


class DiagnosisAnswer(BaseModel):
    """Operator-facing diagnosis bundle with provenance links."""

    model_config = ConfigDict(extra="forbid")

    diagnosis: DiagnosisRecord
    evidence_snapshot_id: UUID
    evidence_snapshot_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    cwa_trace_id: UUID


def compute_diagnosis_response_digest(
    response: DiagnosisResponse,
) -> str:
    """Return a canonical SHA-256 digest for a diagnosis response."""

    return canonical_digest(response.model_dump(mode="json"))


class DiagnosisModel(Protocol):
    """Provider-independent contract for diagnosis models."""

    @property
    def provider_name(self) -> str: ...

    @property
    def model_name(self) -> str: ...

    def diagnose(self, snapshot: CWASnapshot) -> DiagnosisResponse: ...


__all__ = [
    "DiagnosisAnswer",
    "DiagnosisFact",
    "DiagnosisEvidenceUnavailable",
    "DiagnosisInference",
    "DiagnosisModel",
    "DiagnosisModelUnavailable",
    "DiagnosisRecord",
    "DiagnosisResponse",
    "RecommendedInspection",
    "compute_diagnosis_response_digest",
]
