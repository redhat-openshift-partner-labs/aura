"""Every command's evidence is redacted before the CLI stores it (C0.4)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest

from aura.authorization import OperatorRole
from aura.contracts import (
    CommandEnvelope,
    CommandState,
    EvidenceItem,
    EvidenceSnapshot,
)
from aura.services.cluster_state import ClusterStateService
from aura.services.evidence import EvidenceCommandService
from aura.services.inventory import InventoryService
from aura.services.proposal import (
    PipelineProposalService,
    ProposalService,
    ProposalStatusService,
)
from aura.services.workflow import WorkflowService


SERVICES = (
    InventoryService,
    WorkflowService,
    ClusterStateService,
    ProposalService,
    PipelineProposalService,
    ProposalStatusService,
)


class SecretCollector:
    def collect(self, *, command_id: UUID, **_: object) -> EvidenceSnapshot:
        return EvidenceSnapshot(
            snapshot_id=UUID(int=50),
            command_id=command_id,
            collector="test",
            collector_version="test",
            collected_at=datetime(2026, 1, 1, tzinfo=UTC),
            source="test",
            source_version="test",
            items=[
                EvidenceItem(
                    item_id=UUID(int=51),
                    kind="inventory",
                    data={
                        "password": "collector-secret",
                        "credentials": {"token": "collector-token"},
                        "name": "visible",
                    },
                )
            ],
            redactions=[],
        )


def test_every_evidence_command_service_is_covered() -> None:
    assert set(EvidenceCommandService.__subclasses__()) == set(SERVICES)


@pytest.mark.parametrize(
    "service_type",
    SERVICES,
    ids=lambda service: service.__name__,
)
def test_evidence_command_service_redacts_collected_evidence(
    service_type: type[EvidenceCommandService],
) -> None:
    command = CommandEnvelope(
        command_id=UUID(int=1),
        command_type=service_type.spec.command_type,
        operator_id="operator@example.com",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        input={},
        idempotency_key="test-key",
        status=CommandState.RECEIVED,
    )

    result = service_type(collector=SecretCollector()).run(
        command,
        {OperatorRole.ADMIN},
    )

    data = result.snapshot.items[0].data
    assert data["password"] == "[REDACTED]"
    assert data["credentials"]["token"] == "[REDACTED]"
    assert data["name"] == "visible"
    assert result.snapshot.redactions == [
        "items[0].data.credentials.token",
        "items[0].data.password",
    ]
