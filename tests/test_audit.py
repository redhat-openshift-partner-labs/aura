from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from aura.contracts import AuditEvent, AuditEventType


def make_audit_event(**overrides: object) -> AuditEvent:
    values: dict[str, object] = {
        "event_id": "00000000-0000-0000-0000-000000000020",
        "command_id": "00000000-0000-0000-0000-000000000001",
        "event_type": "command_received",
        "occurred_at": datetime(2026, 1, 1, tzinfo=UTC),
        "actor": "operator@example.com",
        "data": {"command_type": "inventory.list"},
    }
    values.update(overrides)
    return AuditEvent.model_validate(values)


def test_valid_audit_event() -> None:
    event = make_audit_event()

    assert event.event_id == UUID("00000000-0000-0000-0000-000000000020")
    assert event.command_id == UUID("00000000-0000-0000-0000-000000000001")
    assert event.event_type is AuditEventType.COMMAND_RECEIVED
    assert event.data == {"command_type": "inventory.list"}


def test_audit_event_round_trips_through_json() -> None:
    event = make_audit_event()

    restored = AuditEvent.model_validate_json(event.model_dump_json())

    assert restored == event


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("event_id", "not-a-uuid"),
        ("command_id", "not-a-uuid"),
        ("event_type", "not-an-event"),
        ("occurred_at", "2026-01-01T00:00:00"),
        ("actor", ""),
        ("actor", "   "),
    ],
)
def test_audit_event_rejects_invalid_fields(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        make_audit_event(**{field: value})


def test_audit_event_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        make_audit_event(unexpected="value")

