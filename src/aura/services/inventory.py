from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, final
from uuid import UUID

from aura.authorization import AuthorizationPolicy, OperatorRole
from aura.contracts import (
    CommandEnvelope,
    CommandState,
    CommandType,
    EvidenceSnapshot,
)
from aura.state import CommandStateMachine


class UnauthorizedCommand(Exception):
    """Raised when an operator cannot issue an inventory command."""


class InvalidCommandType(Exception):
    """Raised when a command is not an inventory command."""


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
class InventoryResult:
    command: CommandEnvelope
    snapshot: EvidenceSnapshot


@final
class InventoryService:
    """Runs read-only inventory collection for an accepted command."""

    def __init__(self, collector: EvidenceCollector) -> None:
        self._collector = collector
        self._authorization_policy = AuthorizationPolicy()
        self._state_machine = CommandStateMachine()

    def list_inventory(
        self,
        command: CommandEnvelope,
        operator_roles: set[OperatorRole],
    ) -> InventoryResult:
        if not self._authorization_policy.can_issue(
            operator_roles,
            CommandType.INVENTORY_LIST,
        ):
            raise UnauthorizedCommand(
                "operator is not authorized to list inventory"
            )

        if command.command_type is not CommandType.INVENTORY_LIST:
            raise InvalidCommandType(
                f"expected inventory.list command, got {command.command_type.value}"
            )

        collecting = command.model_copy(
            update={
                "status": self._state_machine.transition(
                    command.status,
                    CommandState.COLLECTING_EVIDENCE,
                )
            }
        )
        snapshot = self._collector.collect(command_id=command.command_id)
        succeeded = collecting.model_copy(
            update={
                "status": self._state_machine.transition(
                    collecting.status,
                    CommandState.SUCCEEDED,
                )
            }
        )

        return InventoryResult(command=succeeded, snapshot=snapshot)


__all__ = [
    "EvidenceCollector",
    "InventoryResult",
    "InventoryService",
    "InvalidCommandType",
    "UnauthorizedCommand",
]
