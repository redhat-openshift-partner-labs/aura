from __future__ import annotations

import pytest

from aura.contracts import CommandState
from aura.state import CommandStateMachine, InvalidCommandTransition


@pytest.mark.parametrize(
    ("current", "expected"),
    [
        (CommandState.RECEIVED, CommandState.COLLECTING_EVIDENCE),
        (CommandState.COLLECTING_EVIDENCE, CommandState.ASSEMBLING_CONTEXT),
        (CommandState.COLLECTING_EVIDENCE, CommandState.SUCCEEDED),
        (CommandState.COLLECTING_EVIDENCE, CommandState.FAILED),
        (CommandState.COLLECTING_EVIDENCE, CommandState.REFUSED),
        (CommandState.ASSEMBLING_CONTEXT, CommandState.SUCCEEDED),
        (CommandState.ASSEMBLING_CONTEXT, CommandState.MODEL_CALLED),
        (CommandState.ASSEMBLING_CONTEXT, CommandState.FAILED),
        (CommandState.ASSEMBLING_CONTEXT, CommandState.REFUSED),
        (CommandState.MODEL_CALLED, CommandState.SUCCEEDED),
        (CommandState.MODEL_CALLED, CommandState.PROPOSAL_READY),
        (CommandState.MODEL_CALLED, CommandState.FAILED),
        (CommandState.MODEL_CALLED, CommandState.REFUSED),
        (CommandState.PROPOSAL_READY, CommandState.AWAITING_APPROVAL),
        (CommandState.PROPOSAL_READY, CommandState.FAILED),
        (CommandState.AWAITING_APPROVAL, CommandState.APPROVED),
        (CommandState.AWAITING_APPROVAL, CommandState.REJECTED),
        (CommandState.APPROVED, CommandState.EXECUTING),
        (CommandState.EXECUTING, CommandState.SUCCEEDED),
        (CommandState.EXECUTING, CommandState.FAILED),
        (CommandState.FAILED, CommandState.AWAITING_RETRY),
        (CommandState.AWAITING_RETRY, CommandState.EXECUTING),
    ],
)
def test_valid_command_transitions(
    current: CommandState,
    expected: CommandState,
) -> None:
    machine = CommandStateMachine()

    assert machine.can_transition(current, expected)
    assert machine.transition(current, expected) is expected


@pytest.mark.parametrize(
    ("current", "next_state"),
    [
        (CommandState.RECEIVED, CommandState.EXECUTING),
        (CommandState.RECEIVED, CommandState.SUCCEEDED),
        (CommandState.COLLECTING_EVIDENCE, CommandState.APPROVED),
        (CommandState.MODEL_CALLED, CommandState.EXECUTING),
        (CommandState.PROPOSAL_READY, CommandState.EXECUTING),
        (CommandState.EXECUTING, CommandState.APPROVED),
        (CommandState.SUCCEEDED, CommandState.EXECUTING),
        (CommandState.REJECTED, CommandState.AWAITING_APPROVAL),
    ],
)
def test_invalid_command_transitions_are_rejected(
    current: CommandState,
    next_state: CommandState,
) -> None:
    machine = CommandStateMachine()

    assert not machine.can_transition(current, next_state)
    with pytest.raises(InvalidCommandTransition):
        machine.transition(current, next_state)


def test_terminal_states_have_no_outgoing_transitions() -> None:
    machine = CommandStateMachine()

    for terminal in (
        CommandState.SUCCEEDED,
        CommandState.REJECTED,
        CommandState.REFUSED,
    ):
        assert not any(
            machine.can_transition(terminal, candidate)
            for candidate in CommandState
            if candidate is not terminal
        )

