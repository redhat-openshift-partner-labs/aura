from __future__ import annotations

from datetime import UTC, datetime
from typing import final
from uuid import UUID

import pytest

from aura.authorization import OperatorRole
from aura.contracts import (
    CommandEnvelope,
    CommandState,
    CommandType,
    EvidenceSnapshot,
)
from aura.services.cluster_state import (
    ClusterStateService,
    InvalidClusterStateCommand,
    UnauthorizedClusterStateCommand,
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
            collector="cluster_state",
            collector_version="test",
            collected_at=collected_at or datetime(2026, 1, 1, tzinfo=UTC),
            source="git+openshift+tekton",
            source_version=source_version or "test",
            items=[],
            redactions=[],
        )


def make_command(**overrides: object) -> CommandEnvelope:
    values: dict[str, object] = {
        "command_id": UUID(int=1),
        "command_type": CommandType.DIAGNOSE_STATE,
        "operator_id": "operator@example.com",
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
        "input": {"cluster_name": "example"},
        "idempotency_key": "test-key",
    }
    values.update(overrides)
    return CommandEnvelope.model_validate(values)


def test_cluster_state_service_collects_evidence_and_succeeds() -> None:
    service = ClusterStateService(collector=FakeCollector())
    command = make_command()

    result = service.diagnose_state(command, {OperatorRole.VIEWER})

    assert result.command.status is CommandState.ASSEMBLING_CONTEXT
    assert result.snapshot.collector == "cluster_state"


def test_cluster_state_service_rejects_unauthorized_operator() -> None:
    service = ClusterStateService(collector=FakeCollector())
    command = make_command()

    with pytest.raises(UnauthorizedClusterStateCommand):
        service.diagnose_state(command, {OperatorRole.PROPOSER})


def test_cluster_state_service_rejects_wrong_command_type() -> None:
    service = ClusterStateService(collector=FakeCollector())
    command = make_command(command_type=CommandType.INVENTORY_LIST)

    with pytest.raises(InvalidClusterStateCommand):
        service.diagnose_state(command, {OperatorRole.VIEWER})


def test_cluster_state_service_rejects_command_not_in_received_state() -> None:
    service = ClusterStateService(collector=FakeCollector())
    command = make_command(status=CommandState.COLLECTING_EVIDENCE)

    with pytest.raises(InvalidCommandTransition):
        service.diagnose_state(command, {OperatorRole.VIEWER})
