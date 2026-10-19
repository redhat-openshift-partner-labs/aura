from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from aura.cwa import CWASlot, CWASlotName, CWASnapshot
from aura.diagnosis import (
    DiagnosisFact,
    DiagnosisInference,
    DiagnosisModelUnavailable,
    DiagnosisResponse,
    RecommendedInspection,
)
from aura.services.diagnosis import (
    DiagnosisModel,
    DiagnosisResult,
    DiagnosisService,
    FallbackDiagnosisModel,
)


def make_cwa_snapshot() -> CWASnapshot:
    slots = [
        CWASlot(
            name=name,
            content=(
                {
                    "items": [
                        {
                            "item_id": str(UUID(int=51)),
                            "kind": "cluster_definition",
                            "data": {"cluster_name": "example"},
                        }
                    ]
                }
                if name is CWASlotName.EVIDENCE_TOOL_RESULTS
                else {}
            ),
        )
        for name in CWASlotName
    ]
    return CWASnapshot(
        snapshot_id=UUID(int=60),
        command_id=UUID(int=1),
        evidence_snapshot_id=UUID(int=50),
        evidence_snapshot_digest="a" * 64,
        assembler_version="0.1.0",
        assembled_at=datetime(2026, 1, 1, tzinfo=UTC),
        slots=slots,
    )


def make_response(**overrides: object) -> DiagnosisResponse:
    values: dict[str, object] = {
        "summary": "The cluster is provisioned.",
        "facts": [
            DiagnosisFact(
                evidence_id=UUID(int=51),
                observation="ClusterDeployment is provisioned.",
            )
        ],
        "inferences": [
            DiagnosisInference(
                statement="The cluster should be reachable.",
                confidence=0.8,
            )
        ],
        "missing_evidence": ["Current Tekton PipelineRun status"],
        "recommended_inspection": RecommendedInspection(
            description="Inspect the latest PipelineRun."
        ),
        "confidence": 0.8,
        "evidence_ids": [UUID(int=51)],
    }
    values.update(overrides)
    return DiagnosisResponse.model_validate(values)


def test_diagnosis_response_round_trips_through_json() -> None:
    response = make_response()

    restored = DiagnosisResponse.model_validate_json(
        response.model_dump_json()
    )

    assert restored == response


def test_diagnosis_response_rejects_invalid_confidence() -> None:
    with pytest.raises(ValidationError):
        make_response(confidence=1.1)


def test_diagnosis_response_rejects_blank_summary() -> None:
    with pytest.raises(ValidationError):
        make_response(summary="   ")


def test_diagnosis_response_rejects_fact_without_evidence_id() -> None:
    with pytest.raises(ValidationError):
        make_response(evidence_ids=[])


def test_fallback_diagnosis_model_returns_deterministic_response() -> None:
    snapshot = make_cwa_snapshot()
    model = FallbackDiagnosisModel()

    response = model.diagnose(snapshot)

    assert response.summary == "No model diagnosis is available."
    assert response.facts == [
        DiagnosisFact(
            evidence_id=UUID(int=51),
            observation="Collected cluster_definition evidence.",
        )
    ]
    assert response.inferences == []
    assert response.missing_evidence == []
    assert response.recommended_inspection == RecommendedInspection(
        description="Review the collected evidence snapshot."
    )
    assert response.confidence == 0.0
    assert response.evidence_ids == [UUID(int=51)]


def test_diagnosis_service_uses_model_when_available() -> None:
    snapshot = make_cwa_snapshot()
    response = make_response()

    class FakeModel:
        provider_name = "test-provider"
        model_name = "test-model"

        def diagnose(self, snapshot: CWASnapshot) -> DiagnosisResponse:
            return response

    service = DiagnosisService(model=FakeModel())
    result = service.diagnose(snapshot)

    assert result == DiagnosisResult(
        response=response,
        provider_name="test-provider",
        model_name="test-model",
        used_fallback=False,
    )


def test_diagnosis_service_uses_fallback_when_model_is_unavailable() -> None:
    snapshot = make_cwa_snapshot()

    class UnavailableModel:
        provider_name = "test-provider"
        model_name = "test-model"

        def diagnose(self, snapshot: CWASnapshot) -> DiagnosisResponse:
            raise DiagnosisModelUnavailable("model is unavailable")

    service = DiagnosisService(model=UnavailableModel())
    result = service.diagnose(snapshot)

    assert result.used_fallback is True
    assert result.provider_name == "aura"
    assert result.model_name == "deterministic-fallback"
    assert result.response.summary == (
        "No model diagnosis is available."
    )


def test_diagnosis_service_propagates_unexpected_model_errors() -> None:
    snapshot = make_cwa_snapshot()

    class BrokenModel:
        provider_name = "test-provider"
        model_name = "test-model"

        def diagnose(self, snapshot: CWASnapshot) -> DiagnosisResponse:
            raise RuntimeError("unexpected model failure")

    service = DiagnosisService(model=BrokenModel())

    with pytest.raises(RuntimeError):
        service.diagnose(snapshot)
