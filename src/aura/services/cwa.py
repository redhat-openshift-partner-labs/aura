from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, final
from uuid import uuid5

from aura.authorization import OperatorRole
from aura.contracts import (
    AuditEvent,
    AuditEventType,
    CommandEnvelope,
    EvidenceSnapshot,
)
from aura.cwa import CWASlot, CWASlotName, CWASnapshot, CWATrace


class InvalidCWAAssembly(Exception):
    """Raised when CWA assembly inputs are inconsistent or incomplete."""


@dataclass(frozen=True)
class CWAAssemblyPolicy:
    instructions: dict[str, Any]
    capabilities: dict[str, Any]
    output_contract: dict[str, Any]
    knowledge: dict[str, Any]


@dataclass(frozen=True)
class CWAAssemblyRequest:
    command: CommandEnvelope
    evidence_snapshot: EvidenceSnapshot
    audit_event: AuditEvent
    operator_roles: set[OperatorRole]
    policy: CWAAssemblyPolicy
    assembled_at: datetime


@dataclass(frozen=True)
class CWAAssemblyResult:
    snapshot: CWASnapshot
    trace: CWATrace


@final
class CWAAssembler:
    """Assembles a deterministic CWA snapshot and trace for a model call."""

    assembler_version = "0.1.0"

    def assemble(self, request: CWAAssemblyRequest) -> CWAAssemblyResult:
        self._validate(request)

        snapshot_id = uuid5(
            request.command.command_id,
            f"cwa-snapshot:{request.evidence_snapshot.snapshot_id}",
        )
        slots = self._slots(request)
        snapshot = CWASnapshot(
            snapshot_id=snapshot_id,
            command_id=request.command.command_id,
            evidence_snapshot_id=request.evidence_snapshot.snapshot_id,
            evidence_snapshot_digest=(
                request.evidence_snapshot.snapshot_digest
            ),
            assembler_version=self.assembler_version,
            assembled_at=request.assembled_at,
            slots=slots,
        )
        trace = CWATrace(
            trace_id=uuid5(
                request.command.command_id,
                f"cwa-trace:{snapshot.snapshot_id}",
            ),
            command_id=request.command.command_id,
            cwa_snapshot_id=snapshot.snapshot_id,
            cwa_snapshot_digest=snapshot.snapshot_digest,
            evidence_snapshot_id=request.evidence_snapshot.snapshot_id,
            evidence_snapshot_digest=(
                request.evidence_snapshot.snapshot_digest
            ),
            audit_event_id=request.audit_event.event_id,
            assembler_version=self.assembler_version,
            assembled_at=request.assembled_at,
        )

        return CWAAssemblyResult(snapshot=snapshot, trace=trace)

    def _validate(self, request: CWAAssemblyRequest) -> None:
        command_id = request.command.command_id
        if (
            request.evidence_snapshot.command_id != command_id
            or request.audit_event.command_id != command_id
        ):
            raise InvalidCWAAssembly("command IDs do not match")

        if request.audit_event.event_type is not (
            AuditEventType.EVIDENCE_COLLECTED
        ):
            raise InvalidCWAAssembly(
                "audit event must record collected evidence"
            )

        if request.audit_event.actor != request.command.operator_id:
            raise InvalidCWAAssembly("audit actor does not match operator")

        audit_data = request.audit_event.data
        if (
            audit_data.get("snapshot_id")
            != str(request.evidence_snapshot.snapshot_id)
            or audit_data.get("snapshot_digest")
            != request.evidence_snapshot.snapshot_digest
        ):
            raise InvalidCWAAssembly(
                "audit event does not match evidence snapshot"
            )

        if not request.operator_roles:
            raise InvalidCWAAssembly("operator roles are required")

        query = request.command.input.get("query")
        if not isinstance(query, str) or not query.strip():
            raise InvalidCWAAssembly("command input requires a query")

        history = request.command.input.get("history", [])
        if not isinstance(history, list):
            raise InvalidCWAAssembly("command history must be a list")

        if request.assembled_at.tzinfo is None:
            raise InvalidCWAAssembly("assembled_at must include a timezone")

        policy_values = (
            request.policy.instructions,
            request.policy.capabilities,
            request.policy.output_contract,
            request.policy.knowledge,
        )
        if any(not isinstance(value, dict) for value in policy_values):
            raise InvalidCWAAssembly("policy values must be objects")

    def _slots(
        self,
        request: CWAAssemblyRequest,
    ) -> list[CWASlot]:
        query = request.command.input["query"]
        history = request.command.input.get("history", [])
        roles = sorted(
            role.value for role in request.operator_roles
        )

        return [
            CWASlot(
                name=CWASlotName.GOVERNANCE_INSTRUCTIONS,
                content=request.policy.instructions,
            ),
            CWASlot(
                name=CWASlotName.GOVERNANCE_CAPABILITIES,
                content=request.policy.capabilities,
            ),
            CWASlot(
                name=CWASlotName.GOVERNANCE_OUTPUT_CONTRACT,
                content=request.policy.output_contract,
            ),
            CWASlot(
                name=CWASlotName.STATE_TASK,
                content={
                    "command_id": str(request.command.command_id),
                    "command_type": request.command.command_type.value,
                    "status": request.command.status.value,
                    "retry_count": 0,
                },
            ),
            CWASlot(
                name=CWASlotName.STATE_USER,
                content={
                    "operator_id": request.command.operator_id,
                    "roles": roles,
                },
            ),
            CWASlot(
                name=CWASlotName.EVIDENCE_TOOL_RESULTS,
                content={
                    "snapshot_id": str(
                        request.evidence_snapshot.snapshot_id
                    ),
                    "snapshot_digest": (
                        request.evidence_snapshot.snapshot_digest
                    ),
                    "collector": request.evidence_snapshot.collector,
                    "source": request.evidence_snapshot.source,
                    "source_version": (
                        request.evidence_snapshot.source_version
                    ),
                    "items": [
                        item.model_dump(mode="json")
                        for item in request.evidence_snapshot.items
                    ],
                },
            ),
            CWASlot(
                name=CWASlotName.EVIDENCE_KNOWLEDGE,
                content=request.policy.knowledge,
            ),
            CWASlot(
                name=CWASlotName.INTERACTION_QUERY,
                content={"text": query},
            ),
            CWASlot(
                name=CWASlotName.INTERACTION_HISTORY,
                content={"messages": history},
            ),
        ]


__all__ = [
    "CWAAssemblyPolicy",
    "CWAAssemblyRequest",
    "CWAAssemblyResult",
    "CWAAssembler",
    "InvalidCWAAssembly",
]
