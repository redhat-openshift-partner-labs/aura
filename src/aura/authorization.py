"""Authorization rules for Aura commands."""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum
from typing import final

from aura.contracts import CommandType


class OperatorRole(StrEnum):
    """Roles that can be assigned to an Aura operator."""

    VIEWER = "viewer"
    PROPOSER = "proposer"
    APPROVER = "approver"
    EXECUTOR = "executor"
    ADMIN = "admin"


_ISSUE_PERMISSIONS: dict[CommandType, frozenset[OperatorRole]] = {
    CommandType.INVENTORY_LIST: frozenset(
        {OperatorRole.VIEWER, OperatorRole.ADMIN}
    ),
    CommandType.WORKFLOW_INSPECT: frozenset(
        {OperatorRole.VIEWER, OperatorRole.ADMIN}
    ),
    CommandType.DIAGNOSE_STATE: frozenset(
        {OperatorRole.VIEWER, OperatorRole.ADMIN}
    ),
    CommandType.PROPOSE_PATCH: frozenset(
        {OperatorRole.PROPOSER, OperatorRole.ADMIN}
    ),
    CommandType.PROPOSE_PIPELINE: frozenset(
        {OperatorRole.PROPOSER, OperatorRole.ADMIN}
    ),
    CommandType.PROPOSAL_STATUS: frozenset(
        {
            OperatorRole.VIEWER,
            OperatorRole.PROPOSER,
            OperatorRole.ADMIN,
        }
    ),
    CommandType.PROPOSAL_SUBMIT: frozenset(
        {OperatorRole.PROPOSER, OperatorRole.ADMIN}
    ),
    # Phase 3 takes approvers' identity from their signatures (D7);
    # this entry is the Phase 0 role model only.
    CommandType.PROPOSAL_APPROVE: frozenset(
        {OperatorRole.APPROVER, OperatorRole.ADMIN}
    ),
    CommandType.PROPOSAL_REJECT: frozenset(
        {OperatorRole.APPROVER, OperatorRole.ADMIN}
    ),
    CommandType.EXECUTE_PATCH: frozenset(
        {OperatorRole.EXECUTOR, OperatorRole.ADMIN}
    ),
    CommandType.EXECUTE_PIPELINE: frozenset(
        {OperatorRole.EXECUTOR, OperatorRole.ADMIN}
    ),
}


@final
class AuthorizationPolicy:
    """Determines which roles may issue or approve Aura commands."""

    _issue_permissions: dict[CommandType, frozenset[OperatorRole]]

    def __init__(self) -> None:
        self._issue_permissions = dict(_ISSUE_PERMISSIONS)

    def required_roles(self, command_type: CommandType) -> set[OperatorRole]:
        """Return roles that are allowed to issue a command type."""

        return set(self._issue_permissions[command_type])

    def can_issue(
        self,
        roles: Iterable[OperatorRole],
        command_type: CommandType,
    ) -> bool:
        """Return whether any operator role can issue a command type."""

        allowed_roles = self._issue_permissions[command_type]
        return any(role in allowed_roles for role in roles)

    def can_approve(self, roles: Iterable[OperatorRole]) -> bool:
        """Return whether any operator role can approve a proposal."""

        allowed_roles = {OperatorRole.APPROVER, OperatorRole.ADMIN}
        return any(role in allowed_roles for role in roles)


__all__ = [
    "AuthorizationPolicy",
    "OperatorRole",
]

