"""The execution store on PostgreSQL (Phase 3 task 3.7).

These run in the full suite and fail, never skip, without
AURA_TEST_POSTGRES_URL, an administrator URL for a disposable server.
Each test gets a fresh database with the migrations and grants applied,
and two operator logins in role aura_operator.
"""

from __future__ import annotations

import os
import threading
from importlib import resources
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest

from aura.config import PostgreSQLStore
from aura.contracts import AuditEvent, CommandState
from aura.execution.records import ExecutionRecord
from aura.execution.store import (
    ExecutionStore,
    RecordConflict,
    RecordInvalid,
    SchemaVersionError,
    _statements,
    migrations,
)
from test_audit_event_store import make_audit_event
from test_command_store import make_command
from test_evidence_snapshot_store import make_snapshot
from test_execution_records import execution


OPERATORS = ("operator_a", "operator_b")
PASSWORD = "operator-test"


def admin_url() -> str:
    url = os.environ.get("AURA_TEST_POSTGRES_URL")
    if not url:
        pytest.fail(
            "AURA_TEST_POSTGRES_URL is not set; see docs/development.md for "
            "the local PostgreSQL test server"
        )
    return url


@pytest.fixture(scope="session")
def server() -> psycopg.conninfo:
    url = admin_url()
    with psycopg.connect(url, autocommit=True) as admin:
        admin.execute(
            "DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = "
            "'aura_operator') THEN CREATE ROLE aura_operator NOLOGIN; END IF; END $$"
        )
        for name in OPERATORS:
            admin.execute(
                f"DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{name}') "
                f"THEN CREATE ROLE {name} LOGIN PASSWORD '{PASSWORD}' IN ROLE aura_operator; "
                "END IF; END $$"
            )
    return psycopg.conninfo.conninfo_to_dict(url)


@pytest.fixture
def database(server):
    name = f"aura_test_{uuid4().hex[:12]}"
    with psycopg.connect(psycopg.conninfo.make_conninfo(**server), autocommit=True) as admin:
        admin.execute(f"CREATE DATABASE {name}")
    admin_db = psycopg.connect(
        psycopg.conninfo.make_conninfo(**{**server, "dbname": name}), autocommit=True
    )
    grants = (resources.files("aura.execution") / "postgresql" / "grants.sql").read_text(encoding="utf-8")
    for script in [*migrations(), grants]:
        for statement in _statements(script):
            admin_db.execute(statement)
    admin_db.execute(
        "INSERT INTO deployment (singleton, deployment_id) VALUES (1, %s)", (str(uuid4()),)
    )
    yield {"name": name, "admin": admin_db, "server": server}
    admin_db.close()
    with psycopg.connect(psycopg.conninfo.make_conninfo(**server), autocommit=True) as admin:
        admin.execute(f"DROP DATABASE {name} WITH (FORCE)")


def open_store(database, tmp_path: Path, user: str = "operator_a") -> ExecutionStore:
    password = tmp_path / f"{user}.password"
    password.write_text(PASSWORD, encoding="utf-8")
    server = database["server"]
    return ExecutionStore.open_postgresql(
        PostgreSQLStore(
            backend="postgresql",
            host=server.get("host", "localhost"),
            port=int(server.get("port", 5432)),
            database=database["name"],
            user=user,
            password_file=password,
        )
    )


def test_the_store_opens_as_the_operator(database, tmp_path) -> None:
    store = open_store(database, tmp_path)

    assert store.backend == "postgresql"
    assert UUID(store.deployment_id())


@pytest.mark.parametrize(
    ("table", "record", "key"),
    [
        ("commands", make_command("key-a"), {"idempotency_key": "key-a"}),
        ("audit_events", make_audit_event(), {"event_id": make_audit_event().event_id}),
        ("evidence_snapshots", make_snapshot(), {"snapshot_id": make_snapshot().snapshot_id}),
    ],
    ids=["commands", "audit_events", "evidence_snapshots"],
)
def test_a_record_round_trips(database, tmp_path, table, record, key) -> None:
    store = open_store(database, tmp_path)

    store.insert(table, record)
    store.insert(table, record)

    assert store.get(table, **key) == record
    assert store.select(table) == [record]


def test_a_different_record_under_a_stored_key_is_a_conflict(database, tmp_path) -> None:
    store = open_store(database, tmp_path)
    store.insert("commands", make_command("taken"))

    with pytest.raises(RecordConflict, match="commands"):
        store.insert_together([
            ("audit_events", make_audit_event()),
            ("commands", make_command("taken", status=CommandState.FAILED.value)),
        ])

    assert store.select("audit_events") == []


def test_idempotency_keys_are_unique_per_login(database, tmp_path) -> None:
    open_store(database, tmp_path, "operator_a").insert("commands", make_command("shared"))
    second = open_store(database, tmp_path, "operator_b")
    second.insert("commands", make_command("shared"))

    assert second.select("commands") == [make_command("shared")]
    logins = database["admin"].execute("SELECT login FROM commands ORDER BY login").fetchall()
    assert logins == [("operator_a",), ("operator_b",)]


def test_an_operator_cannot_write_another_login(database, tmp_path) -> None:
    store = open_store(database, tmp_path, "operator_a")
    row = store._row(store_table("commands"), make_command("spoof"))
    row["login"] = "operator_b"

    with pytest.raises(psycopg.errors.CheckViolation):
        store._insert(store_table("commands"), row)


def store_table(name):
    from aura.execution.store import TABLES

    return TABLES[name]


def test_a_tampered_row_is_refused(database, tmp_path) -> None:
    store = open_store(database, tmp_path)
    store.insert("audit_events", make_audit_event())
    database["admin"].execute("UPDATE audit_events SET digest = %s", ("0" * 64,))

    with pytest.raises(RecordInvalid, match="digest"):
        store.select("audit_events")


def test_a_schema_version_outside_the_range_is_refused(database, tmp_path) -> None:
    database["admin"].execute("INSERT INTO schema_versions (version) VALUES (99)")

    with pytest.raises(SchemaVersionError, match="schema version 99"):
        open_store(database, tmp_path)


def dispatch(**changes) -> ExecutionRecord:
    from test_approvals import SIGNATURE

    return ExecutionRecord.model_validate(execution(signature=SIGNATURE, **changes))


def test_a_proposal_is_dispatched_at_most_once(database, tmp_path) -> None:
    store = open_store(database, tmp_path)
    store.insert("executions", dispatch())

    with pytest.raises(RecordConflict, match="executions"):
        store.insert("executions", dispatch(
            record_id=str(UUID(int=52)), execution_id=str(UUID(int=53)),
        ))
    store.insert("executions", dispatch(record_id=str(UUID(int=54)), phase="succeeded"))
    with pytest.raises(RecordConflict, match="executions"):
        store.insert("executions", dispatch(
            record_id=str(UUID(int=55)), phase="failed", failure_reason="late",
        ))
    assert store.dispatches_in_last_hour() == 1


def test_two_locked_check_and_inserts_commit_once(database, tmp_path) -> None:
    """Under SERIALIZABLE, two transactions that each check that no event
    of the command exists cannot both commit."""

    open_store(database, tmp_path)
    first_checked = threading.Event()
    second_checked = threading.Event()
    results: dict[str, str] = {}
    event = make_audit_event()
    other = AuditEvent.model_validate(
        {**event.model_dump(mode="json"), "event_id": str(UUID(int=21)), "event_digest": ""}
    )

    def none_for_the_command(reader: ExecutionStore) -> None:
        if reader.select("audit_events", command_id=event.command_id):
            raise RecordConflict("an event for this command is stored")

    def run(name: str, record: AuditEvent, check) -> None:
        try:
            open_store(database, tmp_path).exclusive("audit_events", record, check)
            results[name] = "stored"
        except RecordConflict as error:
            results[name] = str(error)

    def first_check(reader):
        none_for_the_command(reader)
        first_checked.set()
        second_checked.wait(5)

    def second_check(reader):
        none_for_the_command(reader)
        second_checked.set()

    first = threading.Thread(target=run, args=("first", event, first_check))
    first.start()
    first_checked.wait(5)
    second = threading.Thread(target=run, args=("second", other, second_check))
    second.start()
    for thread in (first, second):
        thread.join(30)

    assert sorted(results.values()).count("stored") == 1, results
    assert len(open_store(database, tmp_path).select("audit_events")) == 1


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE audit_events SET digest = 'x'",
        "DELETE FROM audit_events",
        "TRUNCATE audit_events",
        "ALTER TABLE audit_events ADD COLUMN extra TEXT",
        "CREATE TABLE extra (id INTEGER)",
        "INSERT INTO schema_versions (version) VALUES (3)",
    ],
    ids=["update", "delete", "truncate", "alter", "create", "schema-version"],
)
def test_the_operator_role_can_only_insert_and_select(database, tmp_path, statement) -> None:
    store = open_store(database, tmp_path)
    store.insert("audit_events", make_audit_event())

    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        store._connection.execute(statement)
