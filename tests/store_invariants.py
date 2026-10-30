"""Project invariants for Phase 3 commands, checked in the execution store.

The store counterpart of tests/invariants.py: I1 one terminal record,
I2 a linear audit chain whose last event matches the record, I3 an
artifact only with its event and only for a succeeded command.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from aura.contracts import AuditEvent, AuditEventType, CommandState
from aura.execution import store as execution_store
from aura.execution.store import ExecutionStore, StoreWriteFailed


# Artifact tables and the event each artifact follows.
ARTIFACT_EVENTS = {
    "published_proposals": AuditEventType.PROPOSAL_PUBLISHED,
    "approvals": AuditEventType.APPROVAL_RECORDED,
}


def rows(database: Path, sql: str, *parameters: object) -> list[tuple]:
    connection = sqlite3.connect(database)
    try:
        return connection.execute(sql, parameters).fetchall()
    finally:
        connection.close()


def chain(store: ExecutionStore, command_id) -> list[AuditEvent]:
    """Return a command's events in chain order, asserting I2."""

    events = store.select("audit_events", command_id=command_id)
    successors: dict[object, AuditEvent] = {}
    for event in events:
        assert event.previous_event_id not in successors, "forked chain"
        successors[event.previous_event_id] = event
    ordered: list[AuditEvent] = []
    current = successors.get(None)
    while current is not None:
        ordered.append(current)
        current = successors.get(current.event_id)
    assert len(ordered) == len(events), "events outside the chain"
    return ordered


def assert_store_command_invariants(
    store: ExecutionStore,
    database: Path,
    *,
    key: str,
    command_id,
    expect_record: bool = True,
) -> CommandState | None:
    records = store.select("commands", idempotency_key=key)
    events = chain(store, command_id)
    if not expect_record:
        assert records == []
        return None
    assert len(records) == 1, "I1: one terminal record"
    status = records[0].status
    assert status in {CommandState.SUCCEEDED, CommandState.FAILED, CommandState.REFUSED}
    assert events and events[0].event_type is AuditEventType.COMMAND_RECEIVED
    last = events[-1].event_type
    if status is CommandState.FAILED:
        assert last is AuditEventType.COMMAND_FAILED, "I2"
    elif status is CommandState.REFUSED:
        assert last is AuditEventType.COMMAND_REFUSED, "I2"
    else:
        assert last not in {
            AuditEventType.COMMAND_FAILED,
            AuditEventType.COMMAND_REFUSED,
        }, "I2"
    stored_types = {event.event_type for event in events}
    for table, event_type in ARTIFACT_EVENTS.items():
        command_rows = [
            row for (row,) in rows(database, f"SELECT record FROM {table}")
            if str(command_id) in row
        ]
        if command_rows:
            assert status is CommandState.SUCCEEDED, f"I3: {table} after {status}"
            assert event_type in stored_types, f"I3: {table} without its event"
    return status


@dataclass
class StoreWrites:
    labels: list[str]


@contextmanager
def store_write_faults(monkeypatch, fail_at: int | None = None) -> Iterator[StoreWrites]:
    """Record every execution store insert, and fail the one at fail_at
    before it is written."""

    writes = StoreWrites([])
    insert: Callable = execution_store.ExecutionStore.insert

    def recorded(self, table_name, record):
        label = table_name
        if isinstance(record, AuditEvent):
            label = f"{table_name}:{record.event_type.value}"
        index = len(writes.labels)
        writes.labels.append(label)
        if index == fail_at:
            raise StoreWriteFailed(f"injected write failure: {label}")
        return insert(self, table_name, record)

    monkeypatch.setattr(execution_store.ExecutionStore, "insert", recorded)
    yield writes
