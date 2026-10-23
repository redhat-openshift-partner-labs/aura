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


def test_required_roles_are_reported() -> None:
    policy = AuthorizationPolicy()

    assert policy.required_roles(CommandType.EXECUTE_PIPELINE) == {
        OperatorRole.EXECUTOR,
        OperatorRole.ADMIN,
    }


@pytest.mark.parametrize(
    "role",
    [OperatorRole.APPROVER, OperatorRole.EXECUTOR],
)
def test_proposal_status_excludes_approvers_and_executors(
    role: OperatorRole,
) -> None:
    policy = AuthorizationPolicy()

    assert not policy.can_issue({role}, CommandType.PROPOSAL_STATUS)
