from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar, Protocol
from uuid import UUID

from aura.authorization import AuthorizationPolicy, OperatorRole
from aura.contracts import (
    CommandEnvelope,
    CommandState,
    CommandType,
    EvidenceSnapshot,
)
from aura.redaction import redact_snapshot
from aura.state import CommandStateMachine


class EvidenceCollector(Protocol):
    def collect(
        self,
        *,
        command_id: UUID,
        snapshot_id: UUID | None = None,
        collected_at: datetime | None = None,
        source_version: str | None = None,
    ) -> EvidenceSnapshot:
        ...


@dataclass(frozen=True)
class EvidenceCommandResult:
    command: CommandEnvelope
    snapshot: EvidenceSnapshot


@dataclass(frozen=True)
class EvidenceCommandSpec:
    command_type: CommandType
    unauthorized_error: type[Exception]
    invalid_error: type[Exception]
    unauthorized_message: str
    invalid_message: str


def make_evidence_command_spec(
    *,
    command_type: CommandType,
    unauthorized_error: type[Exception],
    invalid_error: type[Exception],
    action: str,
) -> EvidenceCommandSpec:
    """Build the shared command specification for a read-only action."""

    return EvidenceCommandSpec(
        command_type=command_type,
        unauthorized_error=unauthorized_error,
        invalid_error=invalid_error,
        unauthorized_message=f"operator is not authorized to {action}",
        invalid_message=f"expected {command_type.value} command",
    )


class EvidenceCommandService:
    """Shared read-only command execution for evidence collectors."""

    spec: ClassVar[EvidenceCommandSpec]

    def __init__(self, collector: EvidenceCollector) -> None:
        self._collector = collector
        self._authorization_policy = AuthorizationPolicy()
        self._state_machine = CommandStateMachine()

    def run(
        self,
        command: CommandEnvelope,
        operator_roles: set[OperatorRole],
    ) -> EvidenceCommandResult:
        if not self._authorization_policy.can_issue(
            operator_roles,
            self.spec.command_type,
        ):
            raise self.spec.unauthorized_error(
                self.spec.unauthorized_message
            )

        if command.command_type is not self.spec.command_type:
            raise self.spec.invalid_error(
                f"{self.spec.invalid_message}, got "
                f"{command.command_type.value}"
            )

        collecting = command.with_status(
            self._state_machine.transition(
                command.status,
                CommandState.COLLECTING_EVIDENCE,
            )
        )
        snapshot = redact_snapshot(
            self._collector.collect(command_id=command.command_id)
        )
        succeeded = collecting.with_status(
            self._state_machine.transition(
                collecting.status,
                CommandState.SUCCEEDED,
            )
        )

        return EvidenceCommandResult(
            command=succeeded,
            snapshot=snapshot,
        )


__all__ = [
    "EvidenceCollector",
    "EvidenceCommandResult",
    "EvidenceCommandService",
    "EvidenceCommandSpec",
    "make_evidence_command_spec",
]
