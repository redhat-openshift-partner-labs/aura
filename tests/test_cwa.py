from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from aura.cwa import (
    CWASlot,
    CWASlotName,
    CWASnapshot,
    CWATrace,
    compute_cwa_snapshot_digest,
)


def make_slots() -> list[CWASlot]:
    values = {
        CWASlotName.GOVERNANCE_INSTRUCTIONS: {"policy": "read-only"},
        CWASlotName.GOVERNANCE_CAPABILITIES: {"adapters": []},
        CWASlotName.GOVERNANCE_OUTPUT_CONTRACT: {"schema": "diagnosis"},
        CWASlotName.STATE_TASK: {"command": "inventory.list"},
        CWASlotName.STATE_USER: {"operator": "operator@example.com"},
        CWASlotName.EVIDENCE_TOOL_RESULTS: {"snapshot_id": "50"},
        CWASlotName.EVIDENCE_KNOWLEDGE: {"documents": []},
        CWASlotName.INTERACTION_QUERY: {"text": "list clusters"},
        CWASlotName.INTERACTION_HISTORY: {"messages": []},
    }
    return [
        CWASlot(name=name, content=content)
        for name, content in reversed(list(values.items()))
    ]


def make_snapshot(**overrides: object) -> CWASnapshot:
    values: dict[str, object] = {
        "snapshot_id": UUID(int=60),
        "command_id": UUID(int=1),
        "evidence_snapshot_id": UUID(int=50),
        "evidence_snapshot_digest": "a" * 64,
        "assembler_version": "0.1.0",
        "assembled_at": datetime(2026, 1, 1, tzinfo=UTC),
        "slots": make_slots(),
    }
    values.update(overrides)
    return CWASnapshot.model_validate(values)


def make_trace(**overrides: object) -> CWATrace:
    values: dict[str, object] = {
        "trace_id": UUID(int=70),
        "command_id": UUID(int=1),
        "cwa_snapshot_id": UUID(int=60),
        "cwa_snapshot_digest": "b" * 64,
        "evidence_snapshot_id": UUID(int=50),
        "evidence_snapshot_digest": "a" * 64,
        "audit_event_id": UUID(int=20),
        "assembler_version": "0.1.0",
        "assembled_at": datetime(2026, 1, 1, tzinfo=UTC),
    }
    values.update(overrides)
    return CWATrace.model_validate(values)


def test_cwa_snapshot_normalizes_slot_order_and_computes_digest() -> None:
    snapshot = make_snapshot()

    assert [slot.name for slot in snapshot.slots] == sorted(
        CWASlotName,
        key=lambda name: name.value,
    )
    assert snapshot.snapshot_digest == compute_cwa_snapshot_digest(snapshot)
    assert len(snapshot.snapshot_digest) == 64
    assert all(
        character in "0123456789abcdef"
        for character in snapshot.snapshot_digest
    )


def test_cwa_snapshot_digest_is_deterministic() -> None:
    first = make_snapshot()
    second = make_snapshot()

    assert first.snapshot_digest == second.snapshot_digest


def test_cwa_snapshot_detects_digest_tampering() -> None:
    snapshot = make_snapshot()
    value = json.loads(snapshot.model_dump_json())
    value["slots"][0]["content"]["policy"] = "execute-anyway"

    with pytest.raises(ValidationError):
        CWASnapshot.model_validate(value)


def test_cwa_snapshot_requires_all_slots() -> None:
    slots = make_slots()[:-1]

    with pytest.raises(ValidationError):
        make_snapshot(slots=slots)


def test_cwa_snapshot_rejects_duplicate_slots() -> None:
    slots = make_slots()
    slots.append(slots[0])

    with pytest.raises(ValidationError):
        make_snapshot(slots=slots)


def test_cwa_trace_round_trips_through_json() -> None:
    trace = make_trace()

    restored = CWATrace.model_validate_json(trace.model_dump_json())

    assert restored == trace


def test_cwa_trace_rejects_invalid_fields() -> None:
    with pytest.raises(ValidationError):
        make_trace(cwa_snapshot_digest="not-a-digest")
