from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest

from aura.diagnosis import (
    DiagnosisRecord,
    DiagnosisResponse,
    RecommendedInspection,
    compute_diagnosis_response_digest,
)
from aura.diagnosis_store import (
    DiagnosisNotFound,
    JsonDiagnosisStore,
)


def make_response() -> DiagnosisResponse:
    return DiagnosisResponse(
        summary="The cluster is provisioned.",
        recommended_inspection=RecommendedInspection(
            description="Inspect the latest PipelineRun."
        ),
        confidence=0.8,
        evidence_ids=[UUID(int=51)],
    )


def make_record() -> DiagnosisRecord:
    response = make_response()
    return DiagnosisRecord(
        diagnosis_id=UUID(int=80),
        command_id=UUID(int=1),
        cwa_snapshot_id=UUID("00000000-0000-0000-0000-000000000060"),
        cwa_snapshot_digest="b" * 64,
        provider_name="aura",
        model_name="deterministic-fallback",
        used_fallback=True,
        diagnosed_at=datetime(2026, 1, 1, tzinfo=UTC),
        response=response,
    )


def test_json_diagnosis_store_saves_and_loads(tmp_path) -> None:
    store = JsonDiagnosisStore(root=tmp_path / "diagnoses")
    record = make_record()

    path = store.save(record)
    loaded = store.load(record.cwa_snapshot_id)

    assert path == (
        tmp_path
        / "diagnoses"
        / "00000000-0000-0000-0000-000000000060.json"
    )
    assert loaded == record
    assert record.response_digest == (
        compute_diagnosis_response_digest(record.response)
    )
    assert path.read_text(encoding="utf-8") == record.model_dump_json(
        indent=2
    )


def test_json_diagnosis_store_rejects_missing(tmp_path) -> None:
    store = JsonDiagnosisStore(root=tmp_path)

    with pytest.raises(DiagnosisNotFound):
        store.load(UUID(int=99))
