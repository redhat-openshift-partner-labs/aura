from __future__ import annotations

from aura.authorization import OperatorRole
from aura.contracts import CommandEnvelope, CommandType
from aura.services.evidence import (
    EvidenceCollector,
    EvidenceCommandResult,
    EvidenceCommandService,
    make_evidence_command_spec,
)


class UnauthorizedWorkflowCommand(Exception):
    """Raised when an operator cannot inspect workflows."""


class InvalidWorkflowCommand(Exception):
    """Raised when a command is not a workflow inspection command."""


WorkflowResult = EvidenceCommandResult


class WorkflowService(EvidenceCommandService):
    """Runs read-only workflow inspection for an accepted command."""

    spec = make_evidence_command_spec(
        command_type=CommandType.WORKFLOW_INSPECT,
        unauthorized_error=UnauthorizedWorkflowCommand,
        invalid_error=InvalidWorkflowCommand,
        action="inspect workflows",
    )

    def inspect_workflows(
        self,
        command: CommandEnvelope,
        operator_roles: set[OperatorRole],
    ) -> WorkflowResult:
        return self.run(command, operator_roles)


__all__ = [
    "EvidenceCollector",
    "InvalidWorkflowCommand",
    "UnauthorizedWorkflowCommand",
    "WorkflowResult",
    "WorkflowService",
]
