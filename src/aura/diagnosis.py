from __future__ import annotations

from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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


class DiagnosisModel(Protocol):
    """Provider-independent contract for diagnosis models."""

    @property
    def provider_name(self) -> str: ...

    @property
    def model_name(self) -> str: ...

    def diagnose(self, snapshot: CWASnapshot) -> DiagnosisResponse: ...


__all__ = [
    "DiagnosisFact",
    "DiagnosisInference",
    "DiagnosisModel",
    "DiagnosisModelUnavailable",
    "DiagnosisResponse",
    "RecommendedInspection",
]
