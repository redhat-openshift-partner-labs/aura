from __future__ import annotations

from aura.authorization import OperatorRole
from aura.contracts import CommandEnvelope, CommandType
from aura.services.evidence import (
    EvidenceCollector,
    EvidenceCommandResult,
    EvidenceCommandService,
    make_evidence_command_spec,
)


class UnauthorizedCommand(Exception):
    """Raised when an operator cannot issue an inventory command."""


class InvalidCommandType(Exception):
    """Raised when a command is not an inventory command."""


InventoryResult = EvidenceCommandResult


class InventoryService(EvidenceCommandService):
    """Runs read-only inventory collection for an accepted command."""

    spec = make_evidence_command_spec(
        command_type=CommandType.INVENTORY_LIST,
        unauthorized_error=UnauthorizedCommand,
        invalid_error=InvalidCommandType,
        action="list inventory",
    )

    def list_inventory(
        self,
        command: CommandEnvelope,
        operator_roles: set[OperatorRole],
    ) -> InventoryResult:
        return self.run(command, operator_roles)


__all__ = [
    "EvidenceCollector",
    "InventoryResult",
    "InventoryService",
    "InvalidCommandType",
    "UnauthorizedCommand",
]
