"""The Phase 3 execution store on SQLite (task 2.1)."""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import pytest

from aura.config import SQLiteStore
from aura.contracts import AuditEvent, CommandState
from aura.execution.store import (
    SUPPORTED_SCHEMA_VERSIONS,
    ExecutionStore,
    RecordConflict,
    RecordInvalid,
    RecordNotFound,
    SchemaVersionError,
    StoreOutcomeUnknown,
    StoreWriteFailed,
)
from test_audit_event_store import make_audit_event
from test_command_store import make_command
from test_evidence_snapshot_store import make_snapshot


def config(tmp_path: Path, login: str = "operator-a") -> SQLiteStore:
    return SQLiteStore(backend="sqlite", path=tmp_path / "aura.db", login=login)


def open_store(tmp_path: Path, **options) -> ExecutionStore:
    return ExecutionStore.open_sqlite(config(tmp_path, **options))


def raw(tmp_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(tmp_path / "aura.db", isolation_level=None)


RECORDS = [
    ("commands", make_command("key-a")),
    ("audit_events", make_audit_event()),
    ("evidence_snapshots", make_snapshot()),
]


def key_of(table: str, record) -> dict[str, object]:
    return {
        "commands": {"idempotency_key": getattr(record, "idempotency_key", None)},
        "audit_events": {"event_id": getattr(record, "event_id", None)},
        "evidence_snapshots": {"snapshot_id": getattr(record, "snapshot_id", None)},
    }[table]


@pytest.mark.parametrize(("table", "record"), RECORDS, ids=lambda x: x if isinstance(x, str) else "")
def test_a_record_round_trips(tmp_path, table, record) -> None:
    store = open_store(tmp_path)

    store.insert(table, record)

    assert store.get(table, **key_of(table, record)) == record
    assert store.select(table) == [record]


def test_a_new_sqlite_file_is_initialized_once(tmp_path) -> None:
    first = open_store(tmp_path).deployment_id()

    assert UUID(first)
    assert open_store(tmp_path).deployment_id() == first
    versions = raw(tmp_path).execute(
        "SELECT version FROM schema_versions"
    ).fetchall()
    assert versions == [(max(SUPPORTED_SCHEMA_VERSIONS),)]


def test_an_existing_database_without_the_schema_is_refused(tmp_path) -> None:
    raw(tmp_path).execute("CREATE TABLE unrelated (id INTEGER)")

    with pytest.raises(SchemaVersionError, match="no schema version"):
        open_store(tmp_path)


def test_a_schema_version_outside_the_range_is_refused(tmp_path) -> None:
    open_store(tmp_path)
    newer = max(SUPPORTED_SCHEMA_VERSIONS) + 1
    raw(tmp_path).execute(
        "INSERT INTO schema_versions (version) VALUES (?)", (newer,)
    )

    with pytest.raises(SchemaVersionError, match=f"schema version {newer}"):
        open_store(tmp_path)


def test_a_missing_record_is_not_found(tmp_path) -> None:
    with pytest.raises(RecordNotFound, match="audit_events"):
        open_store(tmp_path).get("audit_events", event_id=UUID(int=9))


@pytest.mark.parametrize(
    ("statement", "message"),
    [
        ("UPDATE audit_events SET record = replace(record, 'operator@', 'other@')",
         "digest"),
        ("UPDATE audit_events SET command_id = '00000000-0000-0000-0000-000000000099'",
         "command_id"),
        ("UPDATE audit_events SET digest = '" + "0" * 64 + "'", "digest"),
        ("UPDATE audit_events SET record = '{'", "audit_events"),
    ],
    ids=["record", "column", "digest-column", "unparseable"],
)
def test_a_tampered_row_is_refused_on_load(tmp_path, statement, message) -> None:
    store = open_store(tmp_path)
    store.insert("audit_events", make_audit_event())
    raw(tmp_path).execute(statement)

    with pytest.raises(RecordInvalid, match=message):
        store.get("audit_events", event_id=make_audit_event().event_id)
    with pytest.raises(RecordInvalid):
        store.select("audit_events")


def test_an_identical_insert_is_idempotent(tmp_path) -> None:
    store = open_store(tmp_path)

    store.insert("audit_events", make_audit_event())
    store.insert("audit_events", make_audit_event())

    assert len(store.select("audit_events")) == 1


def test_a_different_record_under_a_stored_key_is_a_conflict(tmp_path) -> None:
    store = open_store(tmp_path)
    store.insert("audit_events", make_audit_event())
    other = AuditEvent.model_validate(
        {**make_audit_event().model_dump(mode="json"),
         "actor": "other@example.com", "event_digest": ""}
    )

    with pytest.raises(RecordConflict, match="audit_events"):
        store.insert("audit_events", other)


def test_idempotency_keys_are_unique_per_login(tmp_path) -> None:
    open_store(tmp_path, login="operator-a").insert(
        "commands", make_command("shared")
    )
    open_store(tmp_path, login="operator-b").insert(
        "commands", make_command("shared")
    )

    assert open_store(tmp_path, login="operator-b").get(
        "commands", idempotency_key="shared"
    ) == make_command("shared")
    rows = raw(tmp_path).execute(
        "SELECT login FROM commands ORDER BY login"
    ).fetchall()
    assert rows == [("operator-a",), ("operator-b",)]


@dataclass
class FailingCommit:
    """Wraps a connection so COMMIT fails, after or before it applies."""

    connection: sqlite3.Connection
    applied: bool
    reads_fail: bool = False
    failed: bool = False

    def execute(self, sql, parameters=()):
        if self.failed and self.reads_fail:
            raise sqlite3.OperationalError("connection lost")
        if sql.strip().upper() == "COMMIT" and not self.failed:
            self.failed = True
            self.connection.execute(sql if self.applied else "ROLLBACK")
            raise sqlite3.OperationalError("connection lost")
        return self.connection.execute(sql, parameters)


def failing_store(tmp_path, **failure) -> ExecutionStore:
    open_store(tmp_path)
    return ExecutionStore(
        FailingCommit(
            sqlite3.connect(tmp_path / "aura.db", isolation_level=None),
            **failure,
        ),
        login="operator-a",
    )


def test_an_applied_write_with_a_lost_acknowledgement_counts(tmp_path) -> None:
    store = failing_store(tmp_path, applied=True)

    store.insert("audit_events", make_audit_event())

    assert open_store(tmp_path).select("audit_events") == [make_audit_event()]


def test_a_write_that_did_not_apply_fails(tmp_path) -> None:
    store = failing_store(tmp_path, applied=False)

    with pytest.raises(StoreWriteFailed, match="connection lost"):
        store.insert("audit_events", make_audit_event())

    assert open_store(tmp_path).select("audit_events") == []


def test_an_unresolvable_write_names_the_record(tmp_path) -> None:
    store = failing_store(tmp_path, applied=True, reads_fail=True)
    event = make_audit_event()

    with pytest.raises(StoreOutcomeUnknown, match=str(event.event_id)):
        store.insert("audit_events", event)


def test_the_store_never_updates_or_deletes(tmp_path) -> None:
    statements: list[str] = []
    store = open_store(tmp_path)
    store._connection.set_trace_callback(statements.append)
    for table, record in RECORDS:
        store.insert(table, record)
        store.insert(table, record)
        store.select(table)
    with pytest.raises(RecordConflict):
        store.insert(
            "commands",
            make_command("key-a", status=CommandState.FAILED.value),
        )

    assert statements
    assert not [
        statement
        for statement in statements
        if statement.lstrip().upper().startswith(("UPDATE", "DELETE"))
    ]


def test_an_exclusive_insert_rechecks_under_a_lock(tmp_path) -> None:
    """Of two check-and-insert transactions for one subject, one commits.

    The first transaction waits, up to a second, for the second one's
    check. Under the lock that check cannot run until the first commits,
    so it sees the first insert; without the lock both checks pass.
    """

    open_store(tmp_path)
    first_checked = threading.Event()
    second_checked = threading.Event()
    results: dict[str, object] = {}
    event = make_audit_event()
    other = AuditEvent.model_validate(
        {**event.model_dump(mode="json"),
         "event_id": str(UUID(int=21)), "event_digest": ""}
    )

    def no_event_for_the_command(reader: ExecutionStore) -> None:
        if reader.select("audit_events", command_id=event.command_id):
            raise RecordConflict("an event for this command is stored")

    def run(name: str, record: AuditEvent, check) -> None:
        try:
            open_store(tmp_path).exclusive("audit_events", record, check)
            results[name] = "stored"
        except Exception as error:
            results[name] = str(error)

    def first_check(reader: ExecutionStore) -> None:
        no_event_for_the_command(reader)
        first_checked.set()
        second_checked.wait(1)

    def second_check(reader: ExecutionStore) -> None:
        second_checked.set()
        no_event_for_the_command(reader)

    first = threading.Thread(target=run, args=("first", event, first_check))
    first.start()
    first_checked.wait(5)
    second = threading.Thread(target=run, args=("second", other, second_check))
    second.start()
    for thread in (first, second):
        thread.join(40)

    assert results == {
        "first": "stored",
        "second": "an event for this command is stored",
    }
    assert open_store(tmp_path).select("audit_events") == [event]


def test_each_login_selects_only_its_own_commands(tmp_path) -> None:
    open_store(tmp_path, login="operator-a").insert(
        "commands", make_command("first")
    )
    store = open_store(tmp_path, login="operator-b")
    store.insert("commands", make_command("second"))

    assert store.select("commands") == [make_command("second")]


def test_a_refused_exclusive_check_writes_nothing(tmp_path) -> None:
    store = open_store(tmp_path)

    def refuse(reader: ExecutionStore) -> None:
        raise RecordConflict("refused")

    with pytest.raises(RecordConflict, match="refused"):
        store.exclusive("audit_events", make_audit_event(), refuse)

    assert store.select("audit_events") == []


def test_publications_and_approvals_round_trip(tmp_path, monkeypatch) -> None:
    from test_approvals import approval, proposal_records
    from aura.approvals import ApprovalRecord, PublishedProposal

    published = PublishedProposal.model_validate(
        proposal_records(tmp_path / "run", monkeypatch)
    )
    record = ApprovalRecord.model_validate(
        approval(proposal_id=str(published.proposal_id))
    )
    store = open_store(tmp_path)

    store.insert("published_proposals", published)
    store.insert("approvals", record)

    assert store.get(
        "published_proposals", publication_id=published.publication_id
    ) == published
    assert store.select(
        "approvals", proposal_id=published.proposal_id, decision="approved"
    ) == [record]
    cluster = raw(tmp_path).execute(
        "SELECT cluster_name FROM published_proposals"
    ).fetchone()
    assert cluster == (published.proposal.cluster_name,)


def test_a_nested_column_that_differs_from_its_record_is_refused(
    tmp_path, monkeypatch
) -> None:
    from test_approvals import proposal_records
    from aura.approvals import PublishedProposal

    published = PublishedProposal.model_validate(
        proposal_records(tmp_path / "run", monkeypatch)
    )
    store = open_store(tmp_path)
    store.insert("published_proposals", published)
    raw(tmp_path).execute("UPDATE published_proposals SET cluster_name = 'other'")

    with pytest.raises(RecordInvalid, match="cluster_name"):
        store.select("published_proposals")


def every_table_record(tmp_path, monkeypatch) -> dict[str, object]:
    from test_approvals import approval, proposal_records
    from aura.approvals import ApprovalRecord, PublishedProposal

    return {
        **dict(RECORDS),
        "published_proposals": PublishedProposal.model_validate(
            proposal_records(tmp_path / "run", monkeypatch)
        ),
        "approvals": ApprovalRecord.model_validate(approval()),
    }


def test_every_table_refuses_a_record_without_its_stored_digest(
    tmp_path, monkeypatch
) -> None:
    from aura.execution.store import TABLES

    records = every_table_record(tmp_path, monkeypatch)
    assert set(records) == set(TABLES)
    store = open_store(tmp_path)
    for name, record in records.items():
        store.insert(name, record)
        digest = TABLES[name].digest
        raw(tmp_path).execute(
            f"UPDATE {name} SET record = json_remove(record, '$.{digest}')"
        )

        with pytest.raises(RecordInvalid, match=f"no stored {digest}"):
            store.select(name)
