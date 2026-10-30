"""`proposal submit` publishes a proposal to the execution store (task 2.4)."""

from __future__ import annotations

import io
import json
from contextlib import redirect_stderr, redirect_stdout
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from aura.cli import main
from aura.config import SQLiteStore
from aura.contracts import AuditEventType, CommandState
from aura.execution.store import ExecutionStore
from failure_injection import run_cli
from store_invariants import assert_store_command_invariants, chain, store_write_faults
from test_failure_matrix import COMMAND_ID, scenario_named


SUBMIT_ID = UUID(int=500)
SUBMITTED = [
    "audit_events:command_received",
    "audit_events:proposal_published",
    "published_proposals",
    "commands",
]


@pytest.fixture
def phase2(tmp_path, monkeypatch) -> Path:
    """Phase 2 records of one stored deprovision proposal."""

    root = tmp_path / "phase2"
    root.mkdir()
    arguments, collector = scenario_named("propose").build(root)
    run = run_cli(
        arguments,
        monkeypatch,
        collector=collector,
        command_id=COMMAND_ID,
        clock=lambda: datetime(2026, 1, 1, tzinfo=UTC),
    )
    assert run.exit_code == 0, run.stderr
    return root


def use_store(tmp_path: Path, monkeypatch, login: str = "operator-a") -> Path:
    database = tmp_path / "aura.db"
    config = tmp_path / f"{login}.toml"
    config.write_text(
        f'[store]\nbackend = "sqlite"\npath = "{database}"\nlogin = "{login}"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("AURA_CONFIG", str(config))
    return database


def proposal_id(root: Path) -> str:
    (path,) = (root / "proposal").glob("*.json")
    return path.stem


def submit(root: Path, key: str = "submit-1", *, role: str = "proposer", pid: str | None = None):
    arguments = [
        "proposal", "submit",
        "--proposal-id", pid or proposal_id(root),
        "--proposal-dir", str(root / "proposal"),
        "--cwa-dir", str(root / "cwa"),
        "--snapshot-dir", str(root / "snapshot"),
        "--audit-dir", str(root / "audit"),
        "--command-dir", str(root / "commands"),
        "--operator", "proposer@example.com",
        "--idempotency-key", key,
        "--role", role,
    ]
    stdout, stderr = io.StringIO(), io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(
            arguments,
            command_id_factory=lambda: SUBMIT_ID,
            clock=lambda: datetime(2026, 10, 30, tzinfo=UTC),
        )
    return code, stdout.getvalue(), stderr.getvalue()


def open_store(database: Path, login: str = "operator-a") -> ExecutionStore:
    return ExecutionStore.open_sqlite(
        SQLiteStore(backend="sqlite", path=database, login=login)
    )


def test_submit_publishes_the_proposal(tmp_path, monkeypatch, phase2) -> None:
    database = use_store(tmp_path, monkeypatch)

    with store_write_faults(monkeypatch) as writes:
        code, stdout, stderr = submit(phase2)

    assert code == 0, stderr
    assert writes.labels == SUBMITTED
    store = open_store(database)
    (published,) = store.select("published_proposals")
    assert json.loads(stdout)["publication_id"] == str(published.publication_id)
    assert str(published.proposal_id) == proposal_id(phase2)
    assert assert_store_command_invariants(
        store, database, key="submit-1", command_id=SUBMIT_ID
    ) is CommandState.SUCCEEDED


def test_another_login_reads_the_publication(tmp_path, monkeypatch, phase2) -> None:
    database = use_store(tmp_path, monkeypatch)
    assert submit(phase2)[0] == 0

    other = open_store(database, login="approver-b")

    assert [p.proposal_id for p in other.select("published_proposals")] == [
        UUID(proposal_id(phase2))
    ]


def refused(database: Path, stderr: str, message: str) -> None:
    assert message in stderr
    store = open_store(database)
    assert assert_store_command_invariants(
        store, database, key="submit-1", command_id=SUBMIT_ID
    ) is CommandState.REFUSED
    assert store.select("published_proposals") == []


def test_a_missing_proposal_is_refused(tmp_path, monkeypatch, phase2) -> None:
    database = use_store(tmp_path, monkeypatch)

    code, _, stderr = submit(phase2, pid=str(UUID(int=1)))

    assert code == 2
    refused(database, stderr, "proposal provenance is invalid")


def test_a_proposal_with_broken_provenance_is_refused(tmp_path, monkeypatch, phase2) -> None:
    database = use_store(tmp_path, monkeypatch)
    for path in (phase2 / "cwa" / "traces").glob("*.json"):
        path.unlink()

    code, _, stderr = submit(phase2)

    assert code == 2
    refused(database, stderr, "CWA trace not found")


def test_a_proposal_without_its_command_record_is_refused(tmp_path, monkeypatch, phase2) -> None:
    database = use_store(tmp_path, monkeypatch)
    for path in (phase2 / "commands").glob("*.json"):
        path.unlink()

    code, _, stderr = submit(phase2)

    assert code == 2
    refused(database, stderr, "command not found")


def test_a_published_proposal_is_not_published_again(tmp_path, monkeypatch, phase2) -> None:
    database = use_store(tmp_path, monkeypatch)
    assert submit(phase2, "submit-0")[0] == 0

    code, _, stderr = submit(phase2)

    assert code == 2
    assert "is already published" in stderr
    store = open_store(database)
    assert len(store.select("published_proposals")) == 1


def test_a_reused_key_exits_before_any_write(tmp_path, monkeypatch, phase2) -> None:
    use_store(tmp_path, monkeypatch)
    assert submit(phase2)[0] == 0

    with store_write_faults(monkeypatch) as writes:
        code, stdout, stderr = submit(phase2)

    assert (code, stdout) == (2, "")
    assert "idempotency key already used: submit-1" in stderr
    assert writes.labels == []


def test_an_unauthorized_role_writes_nothing(tmp_path, monkeypatch, phase2) -> None:
    database = use_store(tmp_path, monkeypatch)

    code, _, stderr = submit(phase2, role="viewer")

    assert code == 2
    assert "cannot submit proposals" in stderr
    assert not database.exists()


def test_a_missing_config_is_an_error(tmp_path, monkeypatch, phase2) -> None:
    monkeypatch.setenv("AURA_CONFIG", str(tmp_path / "missing.toml"))

    code, _, stderr = submit(phase2)

    assert code == 2
    assert "cannot read" in stderr


@pytest.mark.parametrize("fail_at", range(len(SUBMITTED)), ids=SUBMITTED)
def test_each_store_write_failure_fails_closed(tmp_path, monkeypatch, phase2, fail_at) -> None:
    database = use_store(tmp_path, monkeypatch)

    with store_write_faults(monkeypatch, fail_at) as writes:
        code, stdout, stderr = submit(phase2)

    assert (code, stdout) == (2, "")
    assert "injected write failure" in stderr
    store = open_store(database)
    if SUBMITTED[fail_at] in {"published_proposals", "commands"}:
        # The publication and the terminal record are stored together, so
        # either failing stores neither; earlier events stay.
        assert writes.labels == SUBMITTED
        assert store.select("commands") == []
        assert store.select("published_proposals") == []
        return
    assert writes.labels[: fail_at + 1] == SUBMITTED[: fail_at + 1]
    assert writes.labels[fail_at + 1 :] == ["audit_events:command_failed", "commands"]
    if fail_at == 0:
        # As in the JSON failure matrix, `command_failed` links to nothing.
        (record,) = store.select("commands")
        assert record.status is CommandState.FAILED
        (event,) = chain(store, SUBMIT_ID)
        assert event.event_type is AuditEventType.COMMAND_FAILED
        return
    assert assert_store_command_invariants(
        store, database, key="submit-1", command_id=SUBMIT_ID
    ) is CommandState.FAILED
    assert store.select("published_proposals") == []
    events = chain(store, SUBMIT_ID)
    assert events[-1].previous_event_id == events[-2].event_id
