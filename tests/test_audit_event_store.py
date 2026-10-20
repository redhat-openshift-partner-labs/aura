from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest

from aura.audit_store import (
    AuditEventNotFound,
    JsonAuditEventStore,
)
from aura.contracts import AuditEvent, AuditEventType


def make_audit_event() -> AuditEvent:
    return AuditEvent(
        event_id=UUID("00000000-0000-0000-0000-000000000020"),
        command_id=UUID("00000000-0000-0000-0000-000000000001"),
        event_type=AuditEventType.EVIDENCE_COLLECTED,
        occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
        actor="operator@example.com",
        data={"snapshot_id": "00000000-0000-0000-0000-000000000050"},
    )


def test_json_audit_event_store_saves_and_loads(tmp_path) -> None:
    store = JsonAuditEventStore(root=tmp_path / "audit")
    event = make_audit_event()

    path = store.save(event)
    loaded = store.load(event.event_id)

    assert path == (
        tmp_path / "audit" / "00000000-0000-0000-0000-000000000020.json"
    )
    assert loaded == event
    assert path.read_text(encoding="utf-8") == event.model_dump_json(indent=2)


def test_json_audit_event_store_rejects_missing(tmp_path) -> None:
    store = JsonAuditEventStore(root=tmp_path)

    with pytest.raises(AuditEventNotFound):
        store.load(UUID("00000000-0000-0000-0000-000000000099"))
