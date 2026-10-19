from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid5

import pytest

from aura.authorization import OperatorRole
from aura.contracts import (
    AuditEvent,
    AuditEventType,
    CommandEnvelope,
    CommandState,
    CommandType,
    EvidenceItem,
    EvidenceSnapshot,
)
from aura.cwa import CWASlotName, compute_cwa_snapshot_digest
from aura.services.cwa import (
    CWAAssemblyPolicy,
    CWAAssemblyRequest,
    CWAAssembler,
    InvalidCWAAssembly,
)


def make_command(**overrides: object) -> CommandEnvelope:
    values: dict[str, object] = {
        "command_id": UUID(int=1),
        "command_type": CommandType.INVENTORY_LIST,
        "operator_id": "operator@example.com",
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
        "input": {
            "query": "list clusters",
            "history": [],
        },
        "idempotency_key": "test-key",
        "status": CommandState.SUCCEEDED,
    }
    values.update(overrides)
    return CommandEnvelope.model_validate(values)


def make_evidence_snapshot(**overrides: object) -> EvidenceSnapshot:
    values: dict[str, object] = {
        "snapshot_id": UUID(int=50),
        "command_id": UUID(int=1),
        "collector": "git_inventory",
        "collector_version": "0.1.0",
        "collected_at": datetime(2026, 1, 1, tzinfo=UTC),
        "source": "fleet-clusters",
        "source_version": "0123456789abcdef0123456789abcdef01234567",
        "items": [
            EvidenceItem(
                item_id=UUID(int=51),
                kind="cluster_definition",
                data={"cluster_name": "example"},
            )
        ],
        "redactions": [],
    }
    values.update(overrides)
    return EvidenceSnapshot.model_validate(values)


def make_audit_event(**overrides: object) -> AuditEvent:
    snapshot = make_evidence_snapshot()
    values: dict[str, object] = {
        "event_id": UUID(int=20),
        "command_id": UUID(int=1),
        "event_type": AuditEventType.EVIDENCE_COLLECTED,
        "occurred_at": datetime(2026, 1, 1, tzinfo=UTC),
        "actor": "operator@example.com",
        "data": {
            "snapshot_id": str(snapshot.snapshot_id),
            "snapshot_digest": snapshot.snapshot_digest,
            "collector": snapshot.collector,
            "source": snapshot.source,
            "source_version": snapshot.source_version,
            "persisted": True,
        },
    }
    values.update(overrides)
    return AuditEvent.model_validate(values)


def make_policy() -> CWAAssemblyPolicy:
    return CWAAssemblyPolicy(
        instructions={"policy": "read-only"},
        capabilities={"adapters": []},
        output_contract={"schema": "diagnosis"},
        knowledge={"documents": []},
    )


def make_request(**overrides: object) -> CWAAssemblyRequest:
    values: dict[str, object] = {
        "command": make_command(),
        "evidence_snapshot": make_evidence_snapshot(),
        "audit_event": make_audit_event(),
        "operator_roles": {OperatorRole.VIEWER},
        "policy": make_policy(),
        "assembled_at": datetime(2026, 1, 1, tzinfo=UTC),
    }
    values.update(overrides)
    return CWAAssemblyRequest(**values)


def test_cwa_assembler_builds_snapshot_and_trace() -> None:
    request = make_request()
    assembler = CWAAssembler()

    result = assembler.assemble(request)

    expected_snapshot_id = uuid5(
        request.command.command_id,
        f"cwa-snapshot:{request.evidence_snapshot.snapshot_id}",
    )
    expected_trace_id = uuid5(
        request.command.command_id,
        f"cwa-trace:{expected_snapshot_id}",
    )

    assert result.snapshot.snapshot_id == expected_snapshot_id
    assert result.trace.trace_id == expected_trace_id
    assert result.snapshot.command_id == request.command.command_id
    assert result.snapshot.evidence_snapshot_id == (
        request.evidence_snapshot.snapshot_id
    )
    assert result.snapshot.evidence_snapshot_digest == (
        request.evidence_snapshot.snapshot_digest
    )
    assert result.snapshot.snapshot_digest == compute_cwa_snapshot_digest(
        result.snapshot
    )
    assert [slot.name for slot in result.snapshot.slots] == sorted(
        CWASlotName,
        key=lambda name: name.value,
    )

    slot_contents = {
        slot.name: slot.content for slot in result.snapshot.slots
    }
    assert slot_contents[CWASlotName.GOVERNANCE_INSTRUCTIONS] == {
        "policy": "read-only"
    }
    assert slot_contents[CWASlotName.GOVERNANCE_CAPABILITIES] == {
        "adapters": []
    }
    assert slot_contents[CWASlotName.GOVERNANCE_OUTPUT_CONTRACT] == {
        "schema": "diagnosis"
    }
    assert slot_contents[CWASlotName.STATE_TASK] == {
        "command_id": str(request.command.command_id),
        "command_type": request.command.command_type.value,
        "status": request.command.status.value,
        "retry_count": 0,
    }
    assert slot_contents[CWASlotName.STATE_USER] == {
        "operator_id": request.command.operator_id,
        "roles": ["viewer"],
    }
    assert slot_contents[CWASlotName.EVIDENCE_TOOL_RESULTS] == {
        "snapshot_id": str(request.evidence_snapshot.snapshot_id),
        "snapshot_digest": request.evidence_snapshot.snapshot_digest,
        "collector": request.evidence_snapshot.collector,
        "source": request.evidence_snapshot.source,
        "source_version": request.evidence_snapshot.source_version,
        "items": [
            item.model_dump(mode="json")
            for item in request.evidence_snapshot.items
        ],
    }
    assert slot_contents[CWASlotName.EVIDENCE_KNOWLEDGE] == {
        "documents": []
    }
    assert slot_contents[CWASlotName.INTERACTION_QUERY] == {
        "text": "list clusters"
    }
    assert slot_contents[CWASlotName.INTERACTION_HISTORY] == {
        "messages": []
    }

    assert result.trace.command_id == request.command.command_id
    assert result.trace.cwa_snapshot_id == result.snapshot.snapshot_id
    assert result.trace.cwa_snapshot_digest == (
        result.snapshot.snapshot_digest
    )
    assert result.trace.evidence_snapshot_id == (
        request.evidence_snapshot.snapshot_id
    )
    assert result.trace.evidence_snapshot_digest == (
        request.evidence_snapshot.snapshot_digest
    )
    assert result.trace.audit_event_id == request.audit_event.event_id
    assert result.trace.assembler_version == "0.1.0"
    assert result.trace.assembled_at == request.assembled_at
    assert result.trace.model_provider is None
    assert result.trace.model_name is None
    assert result.trace.response_digest is None


def test_cwa_assembler_rejects_mismatched_command_ids() -> None:
    request = make_request(
        evidence_snapshot=make_evidence_snapshot(
            command_id=UUID(int=99)
        )
    )

    with pytest.raises(InvalidCWAAssembly):
        CWAAssembler().assemble(request)


def test_cwa_assembler_rejects_wrong_audit_event_type() -> None:
    request = make_request(
        audit_event=make_audit_event(
            event_type=AuditEventType.COMMAND_RECEIVED
        )
    )

    with pytest.raises(InvalidCWAAssembly):
        CWAAssembler().assemble(request)


def test_cwa_assembler_rejects_audit_snapshot_mismatch() -> None:
    request = make_request(
        audit_event=make_audit_event(
            data={
                "snapshot_id": str(UUID(int=99)),
                "snapshot_digest": "0" * 64,
            }
        )
    )

    with pytest.raises(InvalidCWAAssembly):
        CWAAssembler().assemble(request)


def test_cwa_assembler_rejects_missing_query() -> None:
    request = make_request(
        command=make_command(input={"history": []})
    )

    with pytest.raises(InvalidCWAAssembly):
        CWAAssembler().assemble(request)
