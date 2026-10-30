"""The Phase 3 execution store.

Rows are only inserted: each holds its record's canonical JSON, its digest,
and copies of the fields that constraints and queries use, so an insert-only
database role can store them. Load verifies the digest and every copied
column. SQLite serves development and tests; PostgreSQL is added in task 3.7.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import resources
from typing import Any, Protocol, TypeVar
from uuid import UUID, uuid4, uuid5

from pydantic import BaseModel

from aura.approvals import ApprovalRecord, PublishedProposal
from aura.canonical import canonical_json
from aura.config import SQLiteStore
from aura.contracts import AuditEvent, CommandEnvelope, EvidenceSnapshot
from aura.json_store import parse_record
from aura.execution.records import ExecutionRecord, PipelineRunObservation
from aura.signing import APPROVE, EXECUTE, SignatureError, require_not_future, verify


R = TypeVar("R", bound=BaseModel)
# Migrations only add tables, columns, and indexes, so a client accepts
# every stored version in this range (D13).
SUPPORTED_SCHEMA_VERSIONS = range(1, 3)
BUSY_TIMEOUT_SECONDS = 30


class StoreError(Exception):
    """Base class for execution store errors."""


class SchemaVersionError(StoreError):
    """The stored schema is missing or outside the supported range."""


class RecordNotFound(StoreError):
    pass


class RecordConflict(StoreError):
    """Another record is stored under the same key, or a check refused."""


class RecordInvalid(StoreError):
    """A stored row fails its digest or column checks (I9)."""


class StoreWriteFailed(StoreError):
    """A write failed and read-back shows it was not stored."""


class StoreReadFailed(StoreError):
    """A read of the store failed."""


class StoreOutcomeUnknown(StoreError):
    """A write's outcome is unknown and the row cannot be read back (I4)."""


class Connection(Protocol):
    def execute(self, sql: str, parameters: Any = ..., /) -> sqlite3.Cursor: ...


@dataclass(frozen=True)
class Table:
    name: str
    model: type[BaseModel]
    # Fields that identify a row; the store adds `login` for commands.
    key: tuple[str, ...]
    # Further fields copied into columns for constraints and queries; a
    # dotted path copies a nested field into a column named by its last part.
    columns: tuple[str, ...]
    digest: str
    scoped_by_login: bool = False
    # The signature namespace of a signed record, verified on every load.
    signed: str | None = None
    # The event types that may anchor each record: an event of one of them,
    # named for the record's command and key, must be stored with the
    # record's `audit_event_digest`.
    anchored_by: tuple[str, ...] = ()


TABLES = {
    table.name: table
    for table in (
        Table(
            "commands",
            CommandEnvelope,
            ("idempotency_key",),
            ("command_id",),
            "record_digest",
            scoped_by_login=True,
        ),
        Table(
            "audit_events",
            AuditEvent,
            ("event_id",),
            ("command_id", "previous_event_id"),
            "event_digest",
        ),
        Table(
            "evidence_snapshots",
            EvidenceSnapshot,
            ("snapshot_id",),
            ("command_id",),
            "snapshot_digest",
        ),
        Table(
            "published_proposals",
            PublishedProposal,
            ("publication_id",),
            ("proposal_id", "proposal.cluster_name"),
            "publication_digest",
        ),
        Table(
            "approvals",
            ApprovalRecord,
            ("approval_id",),
            ("proposal_id", "decision", "principal"),
            "approval_digest",
            signed=APPROVE,
            anchored_by=("approval_recorded",),
        ),
        Table(
            "executions",
            ExecutionRecord,
            ("record_id",),
            ("execution_id", "proposal_id", "cluster_name", "phase"),
            "execution_digest",
            signed=EXECUTE,
            anchored_by=("execution_started", "execution_finished", "execution_observed"),
        ),
        Table(
            "pipeline_run_observations",
            PipelineRunObservation,
            ("observation_id",),
            ("execution_id",),
            "observation_digest",
            signed=EXECUTE,
            anchored_by=("execution_observed",),
        ),
    )
}


class ExecutionStore:
    """Insert-only access to the execution store over one connection."""

    def __init__(self, connection: Connection, *, login: str) -> None:
        self._connection = connection
        self._login = login
        self._signers: str | None = None
        self._check_schema()

    def with_signers(self, signers: str) -> "ExecutionStore":
        """Use the hub's allowed-signers list to verify signed records."""

        self._signers = signers
        return self

    @classmethod
    def open_sqlite(cls, config: SQLiteStore) -> "ExecutionStore":
        new = not config.path.exists()
        connection = sqlite3.connect(
            config.path,
            isolation_level=None,
            timeout=BUSY_TIMEOUT_SECONDS,
            check_same_thread=False,
        )
        if new:
            # A new development database applies the migrations itself; in
            # production the administrator applies them (D13).
            _initialize(connection)
        return cls(connection, login=config.login)

    def deployment_id(self) -> str:
        row = self._connection.execute(
            "SELECT deployment_id FROM deployment"
        ).fetchone()
        if row is None:
            raise SchemaVersionError("the store has no deployment ID")
        return str(row[0])

    def insert(self, table_name: str, record: BaseModel) -> None:
        self.insert_together([(table_name, record)])

    def insert_together(self, records: list[tuple[str, BaseModel]]) -> None:
        """Insert records in one transaction: all are stored or none is.

        An identical record already stored counts as stored; when the
        outcome is unknown, every row is read back by its key (I4).
        """

        rows = [(TABLES[name], self._row(TABLES[name], record)) for name, record in records]
        try:
            self._insert_rows(rows)
        except sqlite3.IntegrityError as error:
            self._rollback()
            missing = self._conflicts(rows, error)
            if missing:
                # Rows already stored identically count; store the rest.
                try:
                    self._insert_rows(missing)
                except sqlite3.Error as retry:
                    self._rollback()
                    raise RecordConflict(
                        f"{missing[0][0].name} write conflicts: {retry}"
                    ) from retry
        except sqlite3.Error as error:
            self._rollback()
            for table, row in rows:
                self._read_back(table, row, error)

    def _insert_rows(self, rows: list[tuple[Table, dict[str, Any]]]) -> None:
        self._connection.execute("BEGIN IMMEDIATE")
        for table, row in rows:
            self._insert(table, row)
        self._connection.execute("COMMIT")

    def _conflicts(
        self,
        rows: list[tuple[Table, dict[str, Any]]],
        error: sqlite3.Error,
    ) -> list[tuple[Table, dict[str, Any]]]:
        """Raise for a row whose key holds another record; return the rows
        not stored. A conflict on another unique index names its table."""

        missing = []
        for table, row in rows:
            stored = self._stored_record_text(table, row)
            if stored is None:
                missing.append((table, row))
            elif stored != row["record"]:
                raise RecordConflict(
                    f"{table.name} already stores another record under "
                    f"{self._describe_key(table, row)}"
                )
        if len(missing) == len(rows):
            raise RecordConflict(f"{rows[0][0].name} write conflicts: {error}") from error
        return missing

    def exclusive(
        self,
        table_name: str,
        record: BaseModel,
        check: Callable[["ExecutionStore"], None],
    ) -> None:
        """Run check, then insert, in one locked transaction, so two
        check-and-insert transactions cannot both pass the same check."""

        table = TABLES[table_name]
        row = self._row(table, record)
        try:
            self._connection.execute("BEGIN IMMEDIATE")
        except sqlite3.Error as error:
            raise StoreWriteFailed(f"{table.name} write failed: {error}") from error
        try:
            check(self)
            self._insert(table, row)
        except sqlite3.IntegrityError as error:
            self._rollback()
            self._conflicts([(table, row)], error)
            return
        except sqlite3.Error as error:
            self._rollback()
            raise StoreWriteFailed(f"{table.name} write failed: {error}") from error
        except BaseException:
            self._rollback()
            raise
        try:
            self._connection.execute("COMMIT")
        except sqlite3.Error as error:
            self._rollback()
            self._read_back(table, row, error)

    def get(self, table_name: str, **key: object) -> Any:
        table = TABLES[table_name]
        rows = self._select(table, self._key_values(table, key))
        if not rows:
            raise RecordNotFound(f"{table.name} record not found: {key}")
        return rows[0]

    def select(self, table_name: str, **equal: object) -> list[Any]:
        table = TABLES[table_name]
        values = {name: _text(value) for name, value in equal.items()}
        if table.scoped_by_login:
            # Each login sees only its own commands.
            values["login"] = self._login
        return self._select(table, values)

    def _select(self, table: Table, equal: dict[str, str | None]) -> list[Any]:
        names = [
            "record",
            "digest",
            "stored_at",
            *self._key_columns(table),
            *map(_column, table.columns),
        ]
        where = " AND ".join(f"{name} = ?" for name in equal) or "1 = 1"
        # The key breaks stored_at ties in SQL both backends accept.
        order = ", ".join(("stored_at", *self._key_columns(table)))
        try:
            rows = self._connection.execute(
                f"SELECT {', '.join(names)} FROM {table.name} WHERE {where} "
                f"ORDER BY {order}",
                tuple(equal.values()),
            ).fetchall()
        except sqlite3.Error as error:
            raise StoreReadFailed(f"{table.name} read failed: {error}") from error
        return [self._load(table, dict(zip(names, row))) for row in rows]

    def _load(self, table: Table, row: dict[str, Any]) -> Any:
        try:
            record = parse_record(row["record"], table.model, table.name)
        except ValueError as error:
            raise RecordInvalid(f"{table.name} row is invalid: {error}") from error
        fields = record.model_dump(mode="json")
        if row["digest"] != fields[table.digest]:
            raise RecordInvalid(f"{table.name} row digest does not match its record")
        for path in (*table.key, *table.columns):
            if row[_column(path)] != _field(fields, path):
                raise RecordInvalid(
                    f"{table.name} row column {_column(path)} does not match "
                    "its record"
                )
        if table.signed is not None:
            self._verify(table, record, row["stored_at"])
        if table.anchored_by:
            self._require_anchor(table, fields)
        return record

    def _require_anchor(self, table: Table, fields: dict[str, Any]) -> None:
        subject = fields[table.key[0]]
        for event_type in table.anchored_by:
            event_id = uuid5(UUID(fields["command_id"]), f"{event_type}:{subject}")
            events = self._select(TABLES["audit_events"], {"event_id": str(event_id)})
            if events and events[0].event_digest == fields["audit_event_digest"]:
                return
        raise RecordInvalid(
            f"{table.name} row {subject} has no stored "
            f"{' or '.join(table.anchored_by)} event with its audit_event_digest"
        )

    def _verify(self, table: Table, record: Any, stored_at: object) -> None:
        """Verify a signed record at the time the database stored it, so a
        key retired later still verifies it (D7)."""

        if self._signers is None:
            raise RecordInvalid(
                f"{table.name} rows are signed; reading them needs the "
                "allowed-signers list"
            )
        stored = _stored_time(stored_at)
        try:
            # A record stamped after the database stored it would count
            # past its expiry, so it is refused (D11).
            require_not_future(record.created_at, now=stored)
            verify(
                record.signed_payload(),
                record.signature,
                principal=record.principal,
                namespace=table.signed,
                signers=self._signers,
                at=stored,
            )
        except SignatureError as error:
            raise RecordInvalid(
                f"{table.name} row signature is refused: {error}"
            ) from error

    def _row(self, table: Table, record: BaseModel) -> dict[str, Any]:
        if not isinstance(record, table.model):
            raise TypeError(f"{table.name} stores {table.model.__name__}")
        fields = record.model_dump(mode="json")
        row = {
            _column(path): _field(fields, path)
            for path in (*table.key, *table.columns)
        }
        if table.scoped_by_login:
            row["login"] = self._login
        row["record"] = canonical_json(fields)
        row["digest"] = fields[table.digest]
        return row

    def _insert(self, table: Table, row: dict[str, Any]) -> None:
        self._connection.execute(
            f"INSERT INTO {table.name} ({', '.join(row)}) "
            f"VALUES ({', '.join('?' for _ in row)})",
            tuple(row.values()),
        )

    def _read_back(
        self,
        table: Table,
        row: dict[str, Any],
        error: sqlite3.Error,
    ) -> None:
        """Resolve a write whose outcome is unknown (I4 change, D13)."""

        try:
            stored = self._stored_record_text(table, row)
        except sqlite3.Error as read_error:
            raise StoreOutcomeUnknown(
                f"{table.name} write outcome unknown for "
                f"{self._describe_key(table, row)}: {error}; if that row "
                "was stored, it counts"
            ) from read_error
        if stored is None:
            raise StoreWriteFailed(f"{table.name} write failed: {error}") from error
        if stored != row["record"]:
            raise RecordConflict(
                f"{table.name} already stores another record under "
                f"{self._describe_key(table, row)}"
            ) from error

    def _stored_record_text(self, table: Table, row: dict[str, Any]) -> str | None:
        columns = self._key_columns(table)
        found = self._connection.execute(
            f"SELECT record FROM {table.name} WHERE "
            + " AND ".join(f"{name} = ?" for name in columns),
            tuple(row[name] for name in columns),
        ).fetchone()
        return None if found is None else str(found[0])

    def _key_columns(self, table: Table) -> tuple[str, ...]:
        return ("login", *table.key) if table.scoped_by_login else table.key

    def _key_values(self, table: Table, key: dict[str, object]) -> dict[str, str | None]:
        if set(key) != set(table.key):
            raise ValueError(f"{table.name} is keyed by {', '.join(table.key)}")
        values = {name: _text(value) for name, value in key.items()}
        if table.scoped_by_login:
            values["login"] = self._login
        return values

    def _describe_key(self, table: Table, row: dict[str, Any]) -> str:
        return ", ".join(f"{name}={row[name]}" for name in table.key)

    def _rollback(self) -> None:
        try:
            self._connection.execute("ROLLBACK")
        except sqlite3.Error:
            pass

    def _check_schema(self) -> None:
        try:
            row = self._connection.execute(
                "SELECT MAX(version) FROM schema_versions"
            ).fetchone()
        except sqlite3.Error as error:
            raise SchemaVersionError(
                f"the store has no schema version: {error}"
            ) from error
        version = None if row is None else row[0]
        if version is None:
            raise SchemaVersionError("the store has no schema version")
        if version not in SUPPORTED_SCHEMA_VERSIONS:
            raise SchemaVersionError(
                f"schema version {version} is outside the supported range "
                f"{SUPPORTED_SCHEMA_VERSIONS.start}-"
                f"{SUPPORTED_SCHEMA_VERSIONS.stop - 1}"
            )


def _stored_time(value: object) -> datetime:
    """Parse `stored_at`: SQLite's CURRENT_TIMESTAMP is UTC text."""

    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    return datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)


def _column(path: str) -> str:
    return path.rsplit(".", 1)[-1]


def _field(fields: dict[str, Any], path: str) -> Any:
    value: Any = fields
    for name in path.split("."):
        value = value[name]
    return value


def _text(value: object) -> str | None:
    return None if value is None else str(value)


def migrations() -> list[str]:
    """Return the migration scripts in version order."""

    folder = resources.files("aura.execution") / "migrations"
    return [
        script.read_text(encoding="utf-8")
        for script in sorted(folder.iterdir(), key=lambda item: item.name)
        if script.name.endswith(".sql")
    ]


def _initialize(connection: sqlite3.Connection) -> None:
    connection.execute("BEGIN IMMEDIATE")
    try:
        for script in migrations():
            for statement in _statements(script):
                connection.execute(statement)
        connection.execute(
            "INSERT INTO deployment (singleton, deployment_id) VALUES (1, ?)",
            (str(uuid4()),),
        )
        connection.execute("COMMIT")
    except BaseException:
        connection.execute("ROLLBACK")
        raise


def _statements(script: str) -> list[str]:
    """Split a migration into statements; scripts hold no string literals
    with semicolons."""

    lines = [line for line in script.splitlines() if not line.lstrip().startswith("--")]
    return [part.strip() for part in "\n".join(lines).split(";") if part.strip()]


__all__ = [
    "SUPPORTED_SCHEMA_VERSIONS",
    "TABLES",
    "ExecutionStore",
    "RecordConflict",
    "RecordInvalid",
    "RecordNotFound",
    "SchemaVersionError",
    "StoreError",
    "StoreOutcomeUnknown",
    "StoreReadFailed",
    "StoreWriteFailed",
    "Table",
    "migrations",
]
