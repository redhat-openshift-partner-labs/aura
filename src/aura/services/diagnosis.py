from __future__ import annotations

from dataclasses import dataclass
from typing import final

from aura.contracts import EvidenceItem
from aura.cwa import CWASlotName, CWASnapshot
from aura.diagnosis import (
    DiagnosisFact,
    DiagnosisModel,
    DiagnosisModelUnavailable,
    DiagnosisResponse,
    RecommendedInspection,
)


@dataclass(frozen=True)
class DiagnosisResult:
    response: DiagnosisResponse
    provider_name: str
    model_name: str
    used_fallback: bool


@final
class FallbackDiagnosisModel:
    """Deterministic model used when no provider response is available."""

    provider_name = "aura"
    model_name = "deterministic-fallback"

    def diagnose(self, snapshot: CWASnapshot) -> DiagnosisResponse:
        evidence_items = cwa_evidence_items(snapshot)
        facts = [
            DiagnosisFact(
                evidence_id=item.item_id,
                observation=f"Collected {item.kind} evidence.",
            )
            for item in evidence_items
        ]

        return DiagnosisResponse(
            summary="No model diagnosis is available.",
            facts=facts,
            inferences=[],
            missing_evidence=[],
            recommended_inspection=RecommendedInspection(
                description="Review the collected evidence snapshot."
            ),
            confidence=0.0,
            evidence_ids=[item.item_id for item in evidence_items],
        )

@final
class DiagnosisService:
    """Runs a diagnosis model with a deterministic fallback."""

    def __init__(self, model: DiagnosisModel | None = None) -> None:
        self._model = model
        self._fallback = FallbackDiagnosisModel()

    def diagnose(self, snapshot: CWASnapshot) -> DiagnosisResult:
        if self._model is None:
            return self._fallback_result(snapshot)

        try:
            response = self._model.diagnose(snapshot)
        except DiagnosisModelUnavailable:
            return self._fallback_result(snapshot)

        return DiagnosisResult(
            response=response,
            provider_name=self._model.provider_name,
            model_name=self._model.model_name,
            used_fallback=False,
        )

    def _fallback_result(self, snapshot: CWASnapshot) -> DiagnosisResult:
        return DiagnosisResult(
            response=self._fallback.diagnose(snapshot),
            provider_name=self._fallback.provider_name,
            model_name=self._fallback.model_name,
            used_fallback=True,
        )


__all__ = [
    "cwa_evidence_items",
    "DiagnosisModel",
    "DiagnosisResult",
    "DiagnosisService",
    "FallbackDiagnosisModel",
]


def cwa_evidence_items(snapshot: CWASnapshot) -> list[EvidenceItem]:
    """Return validated evidence items from a CWA snapshot."""

    evidence_slot = next(
        slot
        for slot in snapshot.slots
        if slot.name is CWASlotName.EVIDENCE_TOOL_RESULTS
    )
    values = evidence_slot.content.get("items", [])
    return [EvidenceItem.model_validate(value) for value in values]
