"""Project invariants for Phase 3 commands, checked in the execution store.

The store counterpart of tests/invariants.py: I1 one terminal record,
I2 a linear audit chain whose last event matches the record, I3 an
artifact only with its event and only for a succeeded command. Reading
every row through the store also verifies its digest and columns (I4,
I9); I6-I8 have their own tests.
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


# The event that names each artifact: its type, table, and key field.
ARTIFACT_EVENTS = {
    AuditEventType.PROPOSAL_PUBLISHED: ("published_proposals", "publication_id"),
    AuditEventType.APPROVAL_RECORDED: ("approvals", "approval_id"),
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
    # I3: an artifact the command's events name is stored exactly when the
    # command succeeded, and only after its event.
    for event in events:
        if event.event_type not in ARTIFACT_EVENTS:
            continue
        table, key = ARTIFACT_EVENTS[event.event_type]
        stored = rows(
            database, f"SELECT 1 FROM {table} WHERE {key} = ?", event.data[key]
        )
        assert bool(stored) is (status is CommandState.SUCCEEDED), (
            f"I3: {table} {event.data[key]} after {status}"
        )
    return status


@dataclass
class StoreWrites:
    labels: list[str]


@contextmanager
def store_write_faults(monkeypatch, fail_at: int | None = None) -> Iterator[StoreWrites]:
    """Record every execution store insert, and fail the one at fail_at
    before it is written."""

    writes = StoreWrites([])
    insert_together: Callable = execution_store.ExecutionStore.insert_together

    def recorded(self, records):
        # Rows stored together fail together, before the transaction.
        failed = None
        for table_name, record in records:
            label = table_name
            if isinstance(record, AuditEvent):
                label = f"{table_name}:{record.event_type.value}"
            if len(writes.labels) == fail_at:
                failed = label
            writes.labels.append(label)
        if failed is not None:
            raise StoreWriteFailed(f"injected write failure: {failed}")
        return insert_together(self, records)

    monkeypatch.setattr(
        execution_store.ExecutionStore, "insert_together", recorded
    )
    yield writes
