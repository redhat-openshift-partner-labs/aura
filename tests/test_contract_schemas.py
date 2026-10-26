"""The Pydantic contracts are Aura's command JSON Schemas (D4)."""

from __future__ import annotations

import json

import pytest
from pydantic import BaseModel

from aura.contracts import (
    AuditEvent,
    CommandEnvelope,
    EvidenceItem,
    EvidenceSnapshot,
)
from aura.cwa import CWASnapshot, CWATrace
from aura.diagnosis import DiagnosisRecord, DiagnosisResponse
from aura.proposals import Proposal, ScaffoldRun
from aura.scaffold import ScaffoldRequest


CONTRACTS = (
    CommandEnvelope,
    EvidenceSnapshot,
    EvidenceItem,
    AuditEvent,
    CWASnapshot,
    CWATrace,
    DiagnosisResponse,
    DiagnosisRecord,
    Proposal,
    ScaffoldRun,
    ScaffoldRequest,
)


@pytest.mark.parametrize("mode", ["validation", "serialization"])
@pytest.mark.parametrize("model", CONTRACTS, ids=lambda model: model.__name__)
def test_contract_exports_a_json_schema_of_its_fields(
    model: type[BaseModel],
    mode: str,
) -> None:
    schema = json.loads(json.dumps(model.model_json_schema(mode=mode)))

    assert schema["title"] == model.__name__
    assert schema["type"] == "object"
    assert set(schema["properties"]) == set(model.model_fields)
