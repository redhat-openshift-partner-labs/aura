"""Command lifecycle state transitions."""

from __future__ import annotations

from aura.contracts import CommandState


class InvalidCommandTransition(ValueError):
    """Raised when a command attempts an illegal state transition."""


class CommandStateMachine:
    """Validates transitions between Aura command states."""

    _transitions: dict[CommandState, frozenset[CommandState]] = {
        CommandState.RECEIVED: frozenset(
            {CommandState.COLLECTING_EVIDENCE}
        ),
        CommandState.COLLECTING_EVIDENCE: frozenset(
            {CommandState.ASSEMBLING_CONTEXT}
        ),
        CommandState.ASSEMBLING_CONTEXT: frozenset({CommandState.MODEL_CALLED}),
        CommandState.MODEL_CALLED: frozenset({CommandState.PROPOSAL_READY}),
        CommandState.PROPOSAL_READY: frozenset(
            {CommandState.AWAITING_APPROVAL}
        ),
        CommandState.AWAITING_APPROVAL: frozenset(
            {CommandState.APPROVED, CommandState.REJECTED}
        ),
        CommandState.APPROVED: frozenset({CommandState.EXECUTING}),
        CommandState.EXECUTING: frozenset(
            {CommandState.SUCCEEDED, CommandState.FAILED}
        ),
        CommandState.FAILED: frozenset({CommandState.AWAITING_RETRY}),
        CommandState.AWAITING_RETRY: frozenset({CommandState.EXECUTING}),
        CommandState.SUCCEEDED: frozenset(),
        CommandState.REJECTED: frozenset(),
    }

    def can_transition(
        self,
        current: CommandState,
        next_state: CommandState,
    ) -> bool:
        """Return whether a command may move from one state to another."""

        return next_state in self._transitions[current]

    def transition(
        self,
        current: CommandState,
        next_state: CommandState,
    ) -> CommandState:
        """Return the next state after validating a command transition."""

        if not self.can_transition(current, next_state):
            raise InvalidCommandTransition(
                f"cannot transition from {current.value} to {next_state.value}"
            )
        return next_state


__all__ = [
    "CommandStateMachine",
    "InvalidCommandTransition",
]

