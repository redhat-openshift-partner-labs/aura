from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import final
from uuid import UUID

import pytest

from aura.authorization import OperatorRole
from aura.contracts import (
    CommandEnvelope,
    CommandState,
    CommandType,
    EvidenceItem,
    EvidenceSnapshot,
    compute_snapshot_digest,
)
from aura.services.inventory import (
    InventoryService,
    InvalidCommandType,
    UnauthorizedCommand,
)
from aura.state import InvalidCommandTransition


@final
class FakeCollector:
    def collect(
        self,
        *,
        command_id: UUID,
        snapshot_id: UUID | None = None,
        collected_at: datetime | None = None,
        source_version: str | None = None,
    ) -> EvidenceSnapshot:
        return EvidenceSnapshot(
            snapshot_id=snapshot_id or UUID(int=50),
            command_id=command_id,
            collector="git_inventory",
            collector_version="test",
            collected_at=collected_at or datetime(2026, 1, 1, tzinfo=UTC),
            source="fleet-clusters",
            source_version=source_version or "test",
            items=[],
            redactions=[],
        )


def make_command(**overrides: object) -> CommandEnvelope:
    values: dict[str, object] = {
        "command_id": UUID(int=1),
        "command_type": CommandType.INVENTORY_LIST,
        "operator_id": "operator@example.com",
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
        "input": {},
        "idempotency_key": "test-key",
    }
    values.update(overrides)
    return CommandEnvelope.model_validate(values)


def test_inventory_service_collects_evidence_and_succeeds() -> None:
    service = InventoryService(collector=FakeCollector())
    command = make_command()

    result = service.list_inventory(command, {OperatorRole.VIEWER})

    assert result.command.status is CommandState.ASSEMBLING_CONTEXT
    assert result.snapshot.command_id == command.command_id
    assert result.snapshot.collector == "git_inventory"


def test_inventory_service_redacts_collected_evidence() -> None:
    secret_item = EvidenceItem(
        item_id=UUID(int=51),
        kind="inventory",
        data={
            "password": "collector-secret",
            "credentials": {"token": "collector-token"},
            "existing": {"field": "visible"},
        },
    )
    collector = SimpleNamespace(
        collect=lambda **kwargs: FakeCollector().collect(
            **kwargs
        ).model_copy(
            update={
                "items": [secret_item],
                "redactions": ["existing.field"],
            }
        )
    )
    service = InventoryService(collector=collector)
    command = make_command()

    result = service.list_inventory(command, {OperatorRole.VIEWER})

    item = result.snapshot.items[0]
    assert item.data["password"] == "[REDACTED]"
    assert item.data["credentials"]["token"] == "[REDACTED]"
    assert item.data["existing"]["field"] == "visible"
    assert result.snapshot.redactions == [
        "existing.field",
        "items[0].data.credentials.token",
        "items[0].data.password",
    ]
    assert result.snapshot.snapshot_digest == (
        compute_snapshot_digest(result.snapshot)
    )


def test_inventory_service_rejects_unauthorized_operator() -> None:
    service = InventoryService(collector=FakeCollector())
    command = make_command()

    with pytest.raises(UnauthorizedCommand):
        service.list_inventory(command, {OperatorRole.PROPOSER})


def test_inventory_service_rejects_wrong_command_type() -> None:
    service = InventoryService(collector=FakeCollector())
    command = make_command(command_type=CommandType.DIAGNOSE_STATE)

    with pytest.raises(InvalidCommandType):
        service.list_inventory(command, {OperatorRole.VIEWER})


def test_inventory_service_rejects_command_not_in_received_state() -> None:
    service = InventoryService(collector=FakeCollector())
    command = make_command(status=CommandState.COLLECTING_EVIDENCE)

    with pytest.raises(InvalidCommandTransition):
        service.list_inventory(command, {OperatorRole.VIEWER})

