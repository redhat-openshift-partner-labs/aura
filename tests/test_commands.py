from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from aura.contracts import CommandEnvelope, CommandState, CommandType


def make_command(**overrides: object) -> CommandEnvelope:
    values: dict[str, object] = {
        "command_id": "00000000-0000-0000-0000-000000000001",
        "command_type": "inventory.list",
        "operator_id": "operator@example.com",
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
        "input": {"cluster_name": "example"},
        "idempotency_key": "example-key",
    }
    values.update(overrides)
    return CommandEnvelope.model_validate(values)


def test_valid_command_envelope() -> None:
    command = make_command()

    assert command.command_id == UUID("00000000-0000-0000-0000-000000000001")
    assert command.command_type is CommandType.INVENTORY_LIST
    assert command.status is CommandState.RECEIVED
    assert command.input == {"cluster_name": "example"}


def test_command_envelope_round_trips_through_json() -> None:
    command = make_command()

    restored = CommandEnvelope.model_validate_json(command.model_dump_json())

    assert restored == command


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("command_id", "not-a-uuid"),
        ("command_type", "unknown.command"),
        ("operator_id", ""),
        ("operator_id", "   "),
        ("created_at", "2026-01-01T00:00:00"),
        ("idempotency_key", ""),
        ("idempotency_key", "   "),
    ],
)
def test_command_envelope_rejects_invalid_fields(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        make_command(**{field: value})


def test_command_envelope_rejects_unknown_status() -> None:
    with pytest.raises(ValidationError):
        make_command(status="not-a-status")

