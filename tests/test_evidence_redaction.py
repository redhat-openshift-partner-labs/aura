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
from aura.services.evidence import EvidenceCollector, EvidenceCommandService
from aura.services.inventory import InventoryService
from aura.services.proposal import (
    PipelineProposalService,
    ProposalService,
    ProposalStatusService,
)
from aura.services.workflow import WorkflowService
from invariants import record_files
from test_failure_matrix import SCENARIOS, check, execute, scenario_id, stored
from test_inventory_service import FakeCollector


SECRET_ITEM = EvidenceItem(
    item_id=UUID(int=51),
    kind="inventory",
    data={
        "password": "collector-secret",
        "credentials": {"token": "collector-token"},
        "name": "visible",
    },
)
SECRETS = ("collector-secret", "collector-token")
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
        return FakeCollector().collect(command_id=command_id).model_copy(
            update={"items": [SECRET_ITEM]}
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


class SecretInjectingCollector:
    """Append SECRET_ITEM to every snapshot the wrapped collector returns."""

    def __init__(self, collector: object, calls: list[UUID]) -> None:
        self._collector = collector
        self._calls = calls

    def collect(self, **kwargs: object) -> EvidenceSnapshot:
        snapshot = self._collector.collect(**kwargs)
        self._calls.append(snapshot.snapshot_id)
        return snapshot.model_copy(
            update={"items": [*snapshot.items, SECRET_ITEM]}
        )

    def __getattr__(self, name: str) -> object:
        return getattr(self._collector, name)


@pytest.mark.parametrize("scenario", SCENARIOS, ids=scenario_id)
def test_stored_records_contain_no_collected_secret(
    tmp_path,
    monkeypatch,
    scenario,
) -> None:
    # Every command runs end to end through the CLI with secret-bearing
    # evidence; no stored record or output may contain a secret value.
    calls: list[UUID] = []
    init = EvidenceCommandService.__init__

    def secret_init(self, collector: EvidenceCollector) -> None:
        init(self, SecretInjectingCollector(collector, calls))

    monkeypatch.setattr(EvidenceCommandService, "__init__", secret_init)

    run, before = execute(scenario, tmp_path, monkeypatch)

    assert run.exit_code == scenario.exit_code, run.stderr
    assert check(tmp_path, before).status is scenario.status
    assert calls, "the secret-bearing evidence was never collected"
    leaked = [
        str(path.relative_to(tmp_path))
        for path, content in stored(tmp_path).items()
        if any(secret.encode() in content for secret in SECRETS)
    ]
    output = run.stdout + run.stderr
    leaked += ["output"] if any(s in output for s in SECRETS) else []
    assert leaked == []
    if "EvidenceSnapshot" in scenario.writes:
        [snapshot] = record_files(tmp_path / "snapshot").values()
        assert b"[REDACTED]" in snapshot
