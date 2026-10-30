"""Run Phase 3 commands against the execution store (task 2.4).

A Phase 3 command keeps its terminal record and audit chain in the store,
not in the JSON stores. Its idempotency key is unique per login. The
chain starts with `command_received`, records each artifact after its
event (I3), and ends with the terminal record; a failure stores `failed`
and `command_failed` linked to the last event stored (I1, I2).
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID, uuid5

from pydantic import BaseModel

from aura.contracts import (
    AuditEvent,
    AuditEventType,
    CommandEnvelope,
    CommandState,
)
from aura.execution.store import ExecutionStore, RecordConflict, StoreError
from aura.state import CommandStateMachine


class Refusal(Exception):
    """Raised by a command step to refuse the command with a reason."""


@dataclass
class Chain:
    """Appends a command's audit events and artifacts to the store."""

    store: ExecutionStore
    command: CommandEnvelope
    clock: Callable[[], datetime]
    last: AuditEvent | None = field(default=None)

    def append(
        self,
        event_type: AuditEventType,
        data: dict[str, object],
        subject_id: UUID | None = None,
    ) -> AuditEvent:
        name = event_type.value if subject_id is None else f"{event_type.value}:{subject_id}"
        event = AuditEvent.model_validate(
            {
                "event_id": uuid5(self.command.command_id, name),
                "command_id": self.command.command_id,
                "event_type": event_type,
                "occurred_at": self.clock(),
                "actor": self.command.operator_id,
                "data": data,
                "previous_event_id": None if self.last is None else self.last.event_id,
            }
        )
        self.store.insert("audit_events", event)
        self.last = event
        return event

    def store_artifact(self, table: str, record: BaseModel) -> None:
        self.store.insert(table, record)


Step = Callable[[Chain], str]


def run_store_command(
    store: ExecutionStore,
    command: CommandEnvelope,
    step: Step,
    clock: Callable[[], datetime],
) -> int:
    """Run step for command and print its output; exit 0 or 2."""

    duplicate = _duplicate(store, command)
    if duplicate is not None:
        print(f"aura: {duplicate}", file=sys.stderr)
        return 2
    chain = Chain(store, command, clock)
    try:
        chain.append(
            AuditEventType.COMMAND_RECEIVED,
            {"command_type": command.command_type.value,
             "request_digest": command.request_digest},
        )
        output = step(chain)
    except Refusal as refusal:
        return _close(chain, CommandState.REFUSED, str(refusal))
    except (StoreError, OSError, ValueError) as error:
        return _close(chain, CommandState.FAILED, str(error))
    except Exception as error:
        # Exception text may carry secret values; store only its type (I6).
        reason = f"internal error: {type(error).__name__}"
        return _close(chain, CommandState.FAILED, reason, f"{reason}: {error}")
    code = _store_terminal(chain, CommandState.SUCCEEDED)
    if code == 0:
        print(output)
    return code


def _duplicate(store: ExecutionStore, command: CommandEnvelope) -> str | None:
    existing = store.select("commands", idempotency_key=command.idempotency_key)
    if not existing:
        return None
    if existing[0].request_digest == command.request_digest:
        return f"idempotency key already used: {command.idempotency_key}"
    return (
        "idempotency key already used for another request: "
        f"{command.idempotency_key}"
    )


def _close(
    chain: Chain,
    state: CommandState,
    reason: str,
    message: str | None = None,
) -> int:
    event_type = (
        AuditEventType.COMMAND_REFUSED
        if state is CommandState.REFUSED
        else AuditEventType.COMMAND_FAILED
    )
    try:
        chain.append(event_type, {"status": state.value, "reason": reason})
    except StoreError as error:
        print(f"aura: {message or reason}", file=sys.stderr)
        print(f"aura: {error}", file=sys.stderr)
        _store_terminal(chain, CommandState.FAILED, report=False)
        return 2
    print(f"aura: {message or reason}", file=sys.stderr)
    _store_terminal(chain, state, report=True)
    return 2


def _store_terminal(chain: Chain, state: CommandState, *, report: bool = True) -> int:
    machine = CommandStateMachine()
    command = chain.command.with_status(
        machine.transition(chain.command.status, CommandState.COLLECTING_EVIDENCE)
    )
    command = command.with_status(machine.transition(command.status, state))
    try:
        chain.store.insert("commands", command)
    except RecordConflict as conflict:
        # Another run stored this key first; record that, and no terminal
        # record of this run (I1 change).
        winner = chain.store.get("commands", idempotency_key=command.idempotency_key)
        reason = (
            "idempotency key completed concurrently by command "
            f"{winner.command_id}"
        )
        try:
            chain.append(AuditEventType.COMMAND_FAILED, {"status": "failed", "reason": reason})
        except StoreError:
            pass
        print(f"aura: {reason}", file=sys.stderr)
        return 2
    except StoreError as error:
        if report:
            print(f"aura: {error}", file=sys.stderr)
        return 2
    return 0 if state is CommandState.SUCCEEDED else 2


__all__ = ["Chain", "Refusal", "run_store_command"]
