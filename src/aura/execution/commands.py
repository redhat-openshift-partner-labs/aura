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

from aura.collectors.oc_cli import OpenShiftCLIError
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
    # Stored with the terminal record in one transaction, so a run that
    # does not store its terminal record leaves no artifact (I1, I3).
    artifacts: list[tuple[str, BaseModel]] = field(default_factory=list)

    def append(
        self,
        event_type: AuditEventType,
        data: dict[str, object],
        subject_id: UUID | None = None,
    ) -> AuditEvent:
        return self.store_event(self.event(event_type, data, subject_id))

    def store_event(self, event: AuditEvent) -> AuditEvent:
        self.store.insert("audit_events", event)
        self.last = event
        return event

    def event(
        self,
        event_type: AuditEventType,
        data: dict[str, object],
        subject_id: UUID | None = None,
    ) -> AuditEvent:
        """Build the next event without storing it, so an artifact can
        carry its digest before either is written."""

        name = event_type.value if subject_id is None else f"{event_type.value}:{subject_id}"
        return AuditEvent.model_validate(
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

    def store_artifact(self, table: str, record: BaseModel) -> None:
        self.artifacts.append((table, record))


Step = Callable[[Chain], str]


def run_store_command(
    store: ExecutionStore,
    command: CommandEnvelope,
    step: Step,
    clock: Callable[[], datetime],
) -> int:
    """Run step for command and print its output; exit 0 or 2."""

    try:
        duplicate = _duplicate(store, command)
    except StoreError as error:
        # The key check runs before any write, so nothing is stored (I7).
        print(f"aura: {error}", file=sys.stderr)
        return 2
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
    except (StoreError, OpenShiftCLIError, OSError, ValueError) as error:
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
    artifacts = chain.artifacts if state is CommandState.SUCCEEDED else []
    try:
        chain.store.insert_together([*artifacts, ("commands", command)])
    except RecordConflict as conflict:
        return _lost_race(chain, command, conflict)
    except StoreError as error:
        if report:
            print(f"aura: {error}", file=sys.stderr)
        return 2
    return 0 if state is CommandState.SUCCEEDED else 2


_CLOSING_EVENTS = {AuditEventType.COMMAND_FAILED, AuditEventType.COMMAND_REFUSED}


def _lost_race(chain: Chain, command: CommandEnvelope, conflict: RecordConflict) -> int:
    """Report a terminal insert another record blocked. When another run
    stored this key first, record that run, and no terminal record of this
    one (I1 change); a chain that already closed keeps its last event."""

    try:
        winner = chain.store.get("commands", idempotency_key=command.idempotency_key)
    except StoreError:
        winner = None
    if winner is None or winner.command_id == command.command_id:
        print(f"aura: {conflict}", file=sys.stderr)
        return 2
    reason = f"idempotency key completed concurrently by command {winner.command_id}"
    if chain.last is None or chain.last.event_type not in _CLOSING_EVENTS:
        try:
            chain.append(
                AuditEventType.COMMAND_FAILED,
                {"status": CommandState.FAILED.value, "reason": reason},
                subject_id=winner.command_id,
            )
        except StoreError as error:
            print(f"aura: {error}", file=sys.stderr)
    print(f"aura: {reason}", file=sys.stderr)
    return 2


__all__ = ["Chain", "Refusal", "run_store_command"]
