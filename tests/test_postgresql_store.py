"""The execution store on PostgreSQL (Phase 3 task 3.7).

These run in the full suite and fail, never skip, without
AURA_TEST_POSTGRES_URL, an administrator URL for a disposable server.
Each test gets a fresh database with the migrations and grants applied,
and two operator logins in role aura_operator.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from importlib import resources
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest

from aura.config import PostgreSQLStore
from aura.contracts import AuditEvent, CommandState
from aura.execution.records import ExecutionRecord
from aura.execution.store import (
    POSTGRESQL,
    ExecutionStore,
    RecordConflict,
    RecordInvalid,
    SchemaVersionError,
    StoreOutcomeUnknown,
    StoreWriteFailed,
    _statements,
    migrations,
)
from test_audit_event_store import make_audit_event
from test_command_store import make_command
from test_evidence_snapshot_store import make_snapshot
from test_execution_records import execution
from test_execution_store import (
    executor_signers,
    signed_approval,
    signed_execution,
    signers_for,
)
from test_signing import key


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

    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        store._execute(
            f"INSERT INTO commands ({', '.join(row)}) "
            f"VALUES ({', '.join('?' for _ in row)})",
            tuple(row.values()),
        )


def test_the_grants_can_be_applied_again(database) -> None:
    grants = (resources.files("aura.execution") / "postgresql" / "grants.sql").read_text(encoding="utf-8")
    for statement in _statements(grants):
        database["admin"].execute(statement)


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


def race(database, tmp_path, table: str, records, none_stored, *, signers: str = "") -> dict[str, str]:
    """Run two exclusive inserts whose checks both pass before either
    commits; return each one's outcome."""

    first_checked = threading.Event()
    second_checked = threading.Event()
    results: dict[str, str] = {}

    def run(name: str, record, check) -> None:
        try:
            store = open_store(database, tmp_path).with_signers(signers)
            store.exclusive(table, record, check)
            results[name] = "stored"
        except RecordConflict as error:
            results[name] = str(error)

    def first_check(reader):
        none_stored(reader)
        first_checked.set()
        second_checked.wait(5)

    def second_check(reader):
        none_stored(reader)
        second_checked.set()

    first = threading.Thread(target=run, args=("first", records[0], first_check))
    first.start()
    first_checked.wait(5)
    second = threading.Thread(target=run, args=("second", records[1], second_check))
    second.start()
    for thread in (first, second):
        thread.join(30)
    return results


def test_two_locked_check_and_inserts_commit_once(database, tmp_path) -> None:
    """Under SERIALIZABLE, two transactions that each check that no event
    of the command exists cannot both commit."""

    event = make_audit_event()
    other = AuditEvent.model_validate(
        {**event.model_dump(mode="json"), "event_id": str(UUID(int=21)), "event_digest": ""}
    )

    def none_for_the_command(reader: ExecutionStore) -> None:
        if reader.select("audit_events", command_id=event.command_id):
            raise RecordConflict("an event for this command is stored")

    results = race(database, tmp_path, "audit_events", (event, other), none_for_the_command)

    assert sorted(results.values()).count("stored") == 1, results
    assert len(open_store(database, tmp_path).select("audit_events")) == 1


def test_two_dispatches_for_one_cluster_commit_once(database, tmp_path) -> None:
    """Two proposals of one cluster pass the unique indexes; the G11-style
    check under SERIALIZABLE lets only one dispatch commit."""

    private = key(tmp_path, "executor")
    first, first_event = signed_execution(private)
    second, second_event = signed_execution(
        private,
        record_id=str(UUID(int=62)), execution_id=str(UUID(int=63)),
        proposal_id=str(UUID(int=64)),
    )
    store = open_store(database, tmp_path)
    store.insert("audit_events", first_event)
    store.insert("audit_events", second_event)

    def none_open_for_the_cluster(reader: ExecutionStore) -> None:
        if reader.select("executions", cluster_name="example"):
            raise RecordConflict("the cluster has an open execution")

    results = race(
        database, tmp_path, "executions", (first, second), none_open_for_the_cluster,
        signers=executor_signers(private),
    )

    assert sorted(results.values()).count("stored") == 1, results
    reader = open_store(database, tmp_path).with_signers(executor_signers(private))
    assert len(reader.select("executions")) == 1


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


def test_signed_records_verify_on_load(database, tmp_path) -> None:
    approver = key(tmp_path, "approver")
    executor = key(tmp_path, "executor")
    approval, approval_event = signed_approval(approver)
    record, execution_event = signed_execution(executor)
    store = open_store(database, tmp_path)
    store.insert_together([
        ("audit_events", approval_event),
        ("audit_events", execution_event),
        ("approvals", approval),
        ("executions", record),
    ])

    reader = open_store(database, tmp_path, "operator_b").with_signers(
        signers_for(approver) + executor_signers(executor)
    )

    assert reader.select("approvals") == [approval]
    assert reader.select("executions", cluster_name="example") == [record]


def test_a_forged_signature_is_refused_on_load(database, tmp_path) -> None:
    from aura.approvals import ApprovalRecord
    from test_approvals import SIGNATURE, approval

    approver = key(tmp_path, "approver")
    store = open_store(database, tmp_path)
    store.insert("approvals", ApprovalRecord.model_validate(approval(signature=SIGNATURE)))

    with pytest.raises(RecordInvalid, match="signature"):
        open_store(database, tmp_path).with_signers(signers_for(approver)).select("approvals")


def test_a_tampered_indexed_column_is_refused_on_load(database, tmp_path) -> None:
    executor = key(tmp_path, "executor")
    record, event = signed_execution(executor)
    store = open_store(database, tmp_path)
    store.insert_together([("audit_events", event), ("executions", record)])
    database["admin"].execute("UPDATE executions SET cluster_name = 'other'")

    with pytest.raises(RecordInvalid, match="cluster_name"):
        open_store(database, tmp_path).with_signers(executor_signers(executor)).select("executions")


@dataclass
class FailingCommit:
    """Wraps a connection so COMMIT fails, after or before it applies."""

    connection: psycopg.Connection
    applied: bool
    reads_fail: bool = False
    failed: bool = False

    def execute(self, sql, parameters=()):
        if self.failed and self.reads_fail:
            raise psycopg.OperationalError("connection lost")
        if sql.strip().upper() == "COMMIT" and not self.failed:
            self.failed = True
            self.connection.execute(sql if self.applied else "ROLLBACK")
            raise psycopg.OperationalError("connection lost")
        return self.connection.execute(sql, parameters)


@pytest.mark.parametrize(
    ("failure", "outcome"),
    [
        ({"applied": True}, None),
        ({"applied": False}, StoreWriteFailed),
        ({"applied": True, "reads_fail": True}, StoreOutcomeUnknown),
    ],
    ids=["applied", "not-applied", "unresolvable"],
)
def test_a_lost_commit_acknowledgement_is_resolved_by_reading_back(
    database, tmp_path, failure, outcome,
) -> None:
    connection = open_store(database, tmp_path)._connection
    store = ExecutionStore(
        FailingCommit(connection, **failure), login="operator_a", dialect=POSTGRESQL
    )
    command = make_command("lost")

    if outcome is None:
        store.insert("commands", command)
    else:
        with pytest.raises(outcome, match="connection lost"):
            store.insert("commands", command)

    stored = open_store(database, tmp_path).select("commands")
    assert stored == ([command] if failure["applied"] else [])


def test_the_hourly_window_uses_the_database_clock(database, tmp_path) -> None:
    executor = key(tmp_path, "executor")
    record, event = signed_execution(executor)
    store = open_store(database, tmp_path)
    store.insert_together([("audit_events", event), ("executions", record)])

    assert store.dispatches_in_last_hour() == 1
    database["admin"].execute("UPDATE executions SET stored_at = stored_at - INTERVAL '61 minutes'")
    assert store.dispatches_in_last_hour() == 0


def test_stored_at_is_utc_whatever_the_database_time_zone(database, tmp_path) -> None:
    # UTC+14: a session that kept this zone would store a future stamp.
    database["admin"].execute(f"ALTER DATABASE {database['name']} SET TimeZone = 'Pacific/Kiritimati'")
    store = open_store(database, tmp_path)
    store.insert("audit_events", make_audit_event())

    (stored_at,) = database["admin"].execute("SELECT stored_at FROM audit_events").fetchone()
    assert abs(stored_at - datetime.now(UTC).replace(tzinfo=None)) < timedelta(minutes=1)
    assert store.dispatches_in_last_hour() == 0


def test_the_operator_role_can_insert_into_every_table(database) -> None:
    from aura.execution.store import TABLES

    missing = [
        name for name in TABLES
        if not database["admin"].execute(
            "SELECT has_any_column_privilege('aura_operator', %s, 'INSERT') "
            "AND has_table_privilege('aura_operator', %s, 'SELECT')",
            (name, name),
        ).fetchone()[0]
    ]

    assert missing == []


def test_the_database_clock_is_utc_whatever_the_time_zone(database, tmp_path) -> None:
    database["admin"].execute(f"ALTER DATABASE {database['name']} SET TimeZone = 'Pacific/Kiritimati'")

    now = open_store(database, tmp_path).database_time()

    assert abs(now - datetime.now(UTC)) < timedelta(minutes=1)
