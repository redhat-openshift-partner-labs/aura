"""The Phase 3 execution store on SQLite (task 2.1)."""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid5

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
    assert versions == [(version,) for version in SUPPORTED_SCHEMA_VERSIONS]


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

    with pytest.raises(StoreOutcomeUnknown, match=str(event.event_id)) as error:
        store.insert("audit_events", event)

    assert "if that row was stored, it counts" in str(error.value)


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
    # The approval's test signature is not verifiable; count its row.
    assert raw(tmp_path).execute(
        "SELECT approval_id FROM approvals WHERE proposal_id = ? AND decision = ?",
        (str(published.proposal_id), "approved"),
    ).fetchall() == [(str(record.approval_id),)]
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
    from test_execution_records import execution, observation
    from aura.approvals import ApprovalRecord, PublishedProposal
    from aura.cwa import CWASnapshot, CWATrace
    from aura.execution.records import ExecutionRecord, PipelineRunObservation
    from aura.proposals import Proposal

    bundle = proposal_records(tmp_path / "run", monkeypatch)
    (cwa_snapshot,) = (tmp_path / "run" / "cwa" / "snapshots").glob("*.json")
    return {
        **dict(RECORDS),
        "cwa_snapshots": CWASnapshot.model_validate_json(cwa_snapshot.read_text(encoding="utf-8")),
        "cwa_traces": CWATrace.model_validate(bundle["cwa_trace"]),
        "regenerated_proposals": Proposal.model_validate(bundle["proposal"]),
        "published_proposals": PublishedProposal.model_validate(bundle),
        "approvals": ApprovalRecord.model_validate(approval()),
        "executions": ExecutionRecord.model_validate(execution()),
        "pipeline_run_observations": PipelineRunObservation.model_validate(observation()),
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


def approval_event(**changes) -> AuditEvent:
    from aura.contracts import AuditEventType
    from test_approvals import approval

    fields = approval(**changes)
    return AuditEvent.model_validate(
        {
            "event_id": str(uuid5(UUID(fields["command_id"]), f"approval_recorded:{fields['approval_id']}")),
            "command_id": fields["command_id"],
            "event_type": AuditEventType.APPROVAL_RECORDED,
            "occurred_at": fields["created_at"],
            "actor": fields["principal"],
            "data": {"approval_id": fields["approval_id"]},
        }
    )


def signed_approval(private, *, anchored: bool = True, **changes):
    """A signed approval; with anchored, its audit event is stored first
    by the caller, through the returned event."""

    from aura.approvals import ApprovalRecord
    from aura.signing import APPROVE, sign
    from test_approvals import SIGNATURE, approval

    event = approval_event(**changes)
    changes = {**changes, "audit_event_digest": event.event_digest}
    draft = ApprovalRecord.model_validate(approval(**changes))
    signature = sign(draft.signed_payload(), key=private, namespace=APPROVE)
    return ApprovalRecord.model_validate(
        approval(**changes, signature=signature)
    ), event


@pytest.fixture
def approver_key(tmp_path):
    from test_signing import key

    return key(tmp_path, "approver")


def signers_for(private) -> str:
    from aura.signing import APPROVE
    from test_signing import listed

    return listed("approver@example.com", private, APPROVE)


def test_a_signed_record_verifies_on_load(tmp_path, approver_key) -> None:
    record, event = signed_approval(approver_key)
    store = open_store(tmp_path)
    store.insert("audit_events", event)
    store.insert("approvals", record)

    reader = open_store(tmp_path).with_signers(signers_for(approver_key))

    assert reader.select("approvals") == [record]


def test_a_signed_table_is_unreadable_without_the_signer_list(tmp_path, approver_key) -> None:
    record, _ = signed_approval(approver_key)
    store = open_store(tmp_path)
    store.insert("approvals", record)

    with pytest.raises(RecordInvalid, match="allowed-signers list"):
        open_store(tmp_path).select("approvals")


def test_a_bad_signature_is_refused_on_load(tmp_path, approver_key) -> None:
    from aura.approvals import ApprovalRecord
    from test_approvals import SIGNATURE, approval
    from test_signing import key

    forged = ApprovalRecord.model_validate(approval(signature=SIGNATURE))
    other, _ = signed_approval(key(tmp_path, "other"), approval_id=str(UUID(int=31)))
    store = open_store(tmp_path)
    store.insert("approvals", forged)
    store.insert("approvals", other)
    reader = open_store(tmp_path).with_signers(signers_for(approver_key))

    with pytest.raises(RecordInvalid, match="signature"):
        reader.get("approvals", approval_id=forged.approval_id)
    with pytest.raises(RecordInvalid, match="signature"):
        reader.get("approvals", approval_id=other.approval_id)


def test_a_key_retired_after_storing_still_verifies(tmp_path, approver_key) -> None:
    """Verification runs at `stored_at`, not at the reader's clock: the key
    below was retired after the row was stored and before today."""

    from aura.signing import APPROVE
    from test_signing import listed

    record, event = signed_approval(
        approver_key,
        created_at="2026-10-01T11:00:00+00:00",
        expires_at="2026-10-02T11:00:00+00:00",
    )
    open_store(tmp_path).insert("audit_events", event)
    open_store(tmp_path).insert("approvals", record)
    raw(tmp_path).execute("UPDATE approvals SET stored_at = '2026-10-01 12:00:00'")
    retired = listed("approver@example.com", approver_key, APPROVE, **{"valid-before": "20261015"})
    before = listed("approver@example.com", approver_key, APPROVE, **{"valid-before": "20260930"})

    assert open_store(tmp_path).with_signers(retired).select("approvals") == [record]
    with pytest.raises(RecordInvalid, match="signature"):
        open_store(tmp_path).with_signers(before).select("approvals")


def test_a_record_stamped_after_it_was_stored_is_refused(tmp_path, approver_key) -> None:
    record, event = signed_approval(
        approver_key,
        created_at="2026-10-01T12:05:01+00:00",
        expires_at="2026-10-02T12:05:01+00:00",
    )
    open_store(tmp_path).insert("audit_events", event)
    open_store(tmp_path).insert("approvals", record)
    raw(tmp_path).execute("UPDATE approvals SET stored_at = '2026-10-01 12:00:00'")

    with pytest.raises(RecordInvalid, match="in the future"):
        open_store(tmp_path).with_signers(signers_for(approver_key)).select("approvals")


def test_a_failed_read_is_a_store_error(tmp_path) -> None:
    from aura.execution.store import StoreError

    store = open_store(tmp_path)
    raw(tmp_path).execute("DROP TABLE audit_events")

    with pytest.raises(StoreError, match="audit_events"):
        store.select("audit_events")


def test_store_sql_is_portable_to_postgresql() -> None:
    from aura.execution import store

    source = Path(store.__file__).read_text(encoding="utf-8").lower()

    assert "rowid" not in source


def test_rows_stored_together_are_stored_or_refused_together(tmp_path) -> None:
    store = open_store(tmp_path)
    store.insert("commands", make_command("taken"))

    with pytest.raises(RecordConflict, match="commands"):
        store.insert_together(
            [
                ("audit_events", make_audit_event()),
                ("commands", make_command("taken", status=CommandState.FAILED.value)),
            ]
        )

    assert store.select("audit_events") == []
    store.insert_together([("audit_events", make_audit_event()), ("evidence_snapshots", make_snapshot())])
    assert store.select("audit_events") == [make_audit_event()]
    assert store.select("evidence_snapshots") == [make_snapshot()]


def test_an_exclusive_insert_conflict_is_a_store_error(tmp_path) -> None:
    store = open_store(tmp_path)
    store.insert("audit_events", make_audit_event())
    other = AuditEvent.model_validate(
        {**make_audit_event().model_dump(mode="json"), "actor": "other@example.com", "event_digest": ""}
    )

    with pytest.raises(RecordConflict):
        store.exclusive("audit_events", other, lambda reader: None)


def test_an_approval_without_its_audit_event_is_refused(tmp_path, approver_key) -> None:
    record, _ = signed_approval(approver_key)
    store = open_store(tmp_path)
    store.insert("approvals", record)

    with pytest.raises(RecordInvalid, match="approval_recorded"):
        open_store(tmp_path).with_signers(signers_for(approver_key)).select("approvals")


def signed_execution(private, **changes):
    """A signed ExecutionRecord and the event that anchors it."""

    from aura.contracts import AuditEventType
    from aura.execution.records import ExecutionPhase, ExecutionRecord
    from aura.signing import EXECUTE, sign
    from test_approvals import SIGNATURE
    from test_execution_records import execution

    fields = execution(**changes)
    event_type = (
        AuditEventType.EXECUTION_STARTED
        if fields["phase"] == ExecutionPhase.DISPATCHING
        else AuditEventType.EXECUTION_FINISHED
    )
    event = AuditEvent.model_validate(
        {
            "event_id": str(uuid5(UUID(fields["command_id"]), f"{event_type.value}:{fields['record_id']}")),
            "command_id": fields["command_id"],
            "event_type": event_type,
            "occurred_at": fields["created_at"],
            "actor": fields["principal"],
            "data": {"record_id": fields["record_id"]},
        }
    )
    fields["audit_event_digest"] = event.event_digest
    draft = ExecutionRecord.model_validate({**fields, "signature": SIGNATURE})
    signature = sign(draft.signed_payload(), key=private, namespace=EXECUTE)
    return ExecutionRecord.model_validate({**fields, "signature": signature}), event


@pytest.fixture
def executor_key(tmp_path):
    from test_signing import key

    return key(tmp_path, "executor")


def executor_signers(private) -> str:
    from aura.signing import EXECUTE
    from test_signing import listed

    return listed("executor@example.com", private, EXECUTE)


def store_execution(tmp_path, private, **changes):
    record, event = signed_execution(private, **changes)
    store = open_store(tmp_path)
    store.insert("audit_events", event)
    store.insert("executions", record)
    return record


def test_an_execution_record_round_trips(tmp_path, executor_key) -> None:
    record = store_execution(tmp_path, executor_key)

    reader = open_store(tmp_path).with_signers(executor_signers(executor_key))

    assert reader.select("executions", proposal_id=record.proposal_id) == [record]
    assert reader.select("executions", cluster_name="example") == [record]


def test_an_execution_signed_for_approval_is_refused(tmp_path, executor_key) -> None:
    from aura.signing import APPROVE, EXECUTE
    from test_signing import listed

    store_execution(tmp_path, executor_key)
    approver_only = listed("executor@example.com", executor_key, APPROVE)

    with pytest.raises(RecordInvalid, match="signature"):
        open_store(tmp_path).with_signers(approver_only).select("executions")
    assert EXECUTE != APPROVE


def test_a_proposal_is_dispatched_at_most_once(tmp_path, executor_key) -> None:
    store_execution(tmp_path, executor_key)
    second, event = signed_execution(
        executor_key,
        record_id="00000000-0000-0000-0000-000000000052",
        execution_id="00000000-0000-0000-0000-000000000053",
    )
    store = open_store(tmp_path)
    store.insert("audit_events", event)

    with pytest.raises(RecordConflict, match="executions"):
        store.insert("executions", second)


def test_an_execution_has_at_most_one_terminal_record(tmp_path, executor_key) -> None:
    store_execution(
        tmp_path, executor_key, phase="succeeded",
        record_id="00000000-0000-0000-0000-000000000054",
    )
    failed, event = signed_execution(
        executor_key, phase="failed", failure_reason="late",
        record_id="00000000-0000-0000-0000-000000000055",
    )
    store = open_store(tmp_path)
    store.insert("audit_events", event)

    with pytest.raises(RecordConflict, match="executions"):
        store.insert("executions", failed)
    store_execution(
        tmp_path, executor_key, phase="running",
        record_id="00000000-0000-0000-0000-000000000056",
    )


def test_dispatches_in_the_past_hour_count_by_the_database_clock(tmp_path, executor_key) -> None:
    store_execution(tmp_path, executor_key)
    store = open_store(tmp_path)

    assert store.dispatches_in_last_hour() == 1
    raw(tmp_path).execute("UPDATE executions SET stored_at = datetime('now', '-61 minutes')")
    assert store.dispatches_in_last_hour() == 0


def test_a_signed_observation_loads(tmp_path, executor_key) -> None:
    from aura.contracts import AuditEventType
    from aura.execution.records import PipelineRunObservation
    from aura.signing import EXECUTE, sign
    from test_approvals import SIGNATURE
    from test_execution_records import observation

    fields = observation()
    event = AuditEvent.model_validate(
        {
            "event_id": str(uuid5(UUID(fields["command_id"]), f"execution_observed:{fields['observation_id']}")),
            "command_id": fields["command_id"],
            "event_type": AuditEventType.EXECUTION_OBSERVED,
            "occurred_at": fields["observed_at"],
            "actor": fields["principal"],
            "data": {"observation_id": fields["observation_id"]},
        }
    )
    fields["audit_event_digest"] = event.event_digest
    draft = PipelineRunObservation.model_validate({**fields, "signature": SIGNATURE})
    signature = sign(draft.signed_payload(), key=executor_key, namespace=EXECUTE)
    record = PipelineRunObservation.model_validate({**fields, "signature": signature})
    store = open_store(tmp_path)
    store.insert("audit_events", event)
    store.insert("pipeline_run_observations", record)

    reader = open_store(tmp_path).with_signers(executor_signers(executor_key))

    assert reader.select("pipeline_run_observations") == [record]
