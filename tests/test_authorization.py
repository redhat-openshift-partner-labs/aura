from __future__ import annotations

import pytest

from aura.authorization import AuthorizationPolicy, OperatorRole
from aura.contracts import CommandType


@pytest.mark.parametrize(
    "command_type",
    [
        CommandType.INVENTORY_LIST,
        CommandType.WORKFLOW_INSPECT,
        CommandType.DIAGNOSE_STATE,
        CommandType.PROPOSAL_STATUS,
    ],
)
def test_viewer_can_issue_read_only_commands(command_type: CommandType) -> None:
    policy = AuthorizationPolicy()

    assert policy.can_issue({OperatorRole.VIEWER}, command_type)


@pytest.mark.parametrize(
    "command_type",
    [
        CommandType.PROPOSE_PATCH,
        CommandType.PROPOSE_PIPELINE,
        CommandType.EXECUTE_PATCH,
        CommandType.EXECUTE_PIPELINE,
    ],
)
def test_viewer_cannot_issue_mutating_commands(command_type: CommandType) -> None:
    policy = AuthorizationPolicy()

    assert not policy.can_issue({OperatorRole.VIEWER}, command_type)


@pytest.mark.parametrize(
    "command_type",
    [
        CommandType.PROPOSE_PATCH,
        CommandType.PROPOSE_PIPELINE,
        CommandType.PROPOSAL_STATUS,
    ],
)
def test_proposer_can_issue_proposals(command_type: CommandType) -> None:
    policy = AuthorizationPolicy()

    assert policy.can_issue({OperatorRole.PROPOSER}, command_type)


@pytest.mark.parametrize(
    "command_type",
    [
        CommandType.EXECUTE_PATCH,
        CommandType.EXECUTE_PIPELINE,
    ],
)
def test_proposer_cannot_execute(command_type: CommandType) -> None:
    policy = AuthorizationPolicy()

    assert not policy.can_issue({OperatorRole.PROPOSER}, command_type)


@pytest.mark.parametrize(
    "command_type",
    [
        CommandType.EXECUTE_PATCH,
        CommandType.EXECUTE_PIPELINE,
    ],
)
def test_executor_can_execute(command_type: CommandType) -> None:
    policy = AuthorizationPolicy()

    assert policy.can_issue({OperatorRole.EXECUTOR}, command_type)


def test_approver_can_approve() -> None:
    policy = AuthorizationPolicy()

    assert policy.can_approve({OperatorRole.APPROVER})


def test_viewer_cannot_approve() -> None:
    policy = AuthorizationPolicy()

    assert not policy.can_approve({OperatorRole.VIEWER})


def test_empty_roles_are_denied() -> None:
    policy = AuthorizationPolicy()

    assert not policy.can_issue(set(), CommandType.INVENTORY_LIST)
    assert not policy.can_approve(set())


@pytest.mark.parametrize(
    ("command_type", "roles"),
    [
        (
            CommandType.EXECUTE_PIPELINE,
            {OperatorRole.EXECUTOR, OperatorRole.ADMIN},
        ),
        (
            CommandType.PROPOSAL_STATUS,
            {OperatorRole.VIEWER, OperatorRole.PROPOSER, OperatorRole.ADMIN},
        ),
    ],
)
def test_required_roles_are_reported(
    command_type: CommandType,
    roles: set[OperatorRole],
) -> None:
    policy = AuthorizationPolicy()

    assert policy.required_roles(command_type) == roles


# The Phase 0 role model: viewer reads, proposer proposes, approver only
# approves, executor executes, and admin may do everything.
ALLOWED_ROLES = {
    CommandType.INVENTORY_LIST: {OperatorRole.VIEWER, OperatorRole.ADMIN},
    CommandType.WORKFLOW_INSPECT: {OperatorRole.VIEWER, OperatorRole.ADMIN},
    CommandType.DIAGNOSE_STATE: {OperatorRole.VIEWER, OperatorRole.ADMIN},
    CommandType.PROPOSE_PATCH: {OperatorRole.PROPOSER, OperatorRole.ADMIN},
    CommandType.PROPOSE_PIPELINE: {OperatorRole.PROPOSER, OperatorRole.ADMIN},
    CommandType.PROPOSAL_STATUS: {
        OperatorRole.VIEWER,
        OperatorRole.PROPOSER,
        OperatorRole.ADMIN,
    },
    CommandType.EXECUTE_PATCH: {OperatorRole.EXECUTOR, OperatorRole.ADMIN},
    CommandType.EXECUTE_PIPELINE: {OperatorRole.EXECUTOR, OperatorRole.ADMIN},
}


def test_role_table_covers_every_command_type() -> None:
    assert set(ALLOWED_ROLES) == set(CommandType)


@pytest.mark.parametrize("role", list(OperatorRole))
@pytest.mark.parametrize("command_type", list(CommandType))
def test_each_role_may_issue_only_its_command_types(
    command_type: CommandType,
    role: OperatorRole,
) -> None:
    allowed = role in ALLOWED_ROLES[command_type]

    assert AuthorizationPolicy().can_issue({role}, command_type) is allowed


@pytest.mark.parametrize("role", list(OperatorRole))
def test_only_approver_or_admin_may_approve(role: OperatorRole) -> None:
    allowed = role in {OperatorRole.APPROVER, OperatorRole.ADMIN}

    assert AuthorizationPolicy().can_approve({role}) is allowed
