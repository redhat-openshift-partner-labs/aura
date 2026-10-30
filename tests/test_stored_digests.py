"""Every store verifies a record's own digest on load (I9).

Model validation recomputes a blank digest, so a store must require the
stored value before validating; otherwise an edited record with its
digest removed loads as genuine.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import pkgutil
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import BaseModel

import aura
from aura.command_store import (
    CommandRecordUnreadable,
    JsonCommandStore,
)
from aura.contracts import (
    AuditEvent,
    CommandEnvelope,
    CommandState,
    EvidenceSnapshot,
)
from aura.cwa import CWASnapshot, CWATrace
from aura.diagnosis import DiagnosisRecord
from aura.json_store import JsonRecordStore
from aura.proposals import Proposal
from aura.approvals import ApprovalRecord, PublishedProposal
from aura.execution.records import ExecutionRecord, PipelineRunObservation
from aura.execution.store import TABLES
from failure_injection import run_cli
from test_failure_matrix import COMMAND_ID, KEY, scenario_named
from test_inventory_service import make_command


SELF_DIGESTS = {
    CommandEnvelope: ("request_digest", "record_digest"),
    EvidenceSnapshot: ("snapshot_digest",),
    AuditEvent: ("event_digest",),
    CWASnapshot: ("snapshot_digest",),
    CWATrace: ("trace_digest",),
    DiagnosisRecord: ("response_digest",),
    Proposal: ("proposal_digest",),
    PublishedProposal: ("publication_digest",),
    ApprovalRecord: ("approval_digest",),
    ExecutionRecord: ("execution_digest",),
    PipelineRunObservation: ("observation_digest",),
}


def discovered(base: type) -> set[type]:
    """Return the subclasses of base defined in any Aura module, so a new
    contract or store is checked without being listed here."""

    found: set[type] = set()
    for info in pkgutil.walk_packages(aura.__path__, "aura."):
        module = importlib.import_module(info.name)
        found.update(
            value
            for value in vars(module).values()
            if isinstance(value, type)
            and issubclass(value, base)
            and value is not base
            and value.__module__ == module.__name__
        )
    return found


CONTRACTS = discovered(BaseModel)
STORE_TYPES = discovered(JsonRecordStore)


def test_self_digest_table_covers_every_contract() -> None:
    computed = {
        model: tuple(
            name
            for name, field in model.model_fields.items()
            if name.endswith("_digest") and field.default == ""
        )
        for model in CONTRACTS
    }

    assert {model: names for model, names in computed.items() if names} == (
        SELF_DIGESTS
    )


def store_directories(
    root: Path,
    paths: list[Path],
) -> dict[str, type[JsonRecordStore]]:
    """Map each stored record directory to the store that loads it."""

    directories: dict[str, type[JsonRecordStore]] = {}
    for path in paths:
        directory = "/".join(path.relative_to(root).parts[1:-1])
        for store_type in STORE_TYPES:
            try:
                store_type(path.parent).load(UUID(path.stem))
            except ValueError:
                continue
            directories[directory] = store_type
    assert set(directories.values()) == STORE_TYPES, (
        "a store has no record in the scenario runs"
    )
    return directories


def stored_records(root: Path, monkeypatch) -> list[Path]:
    for name in ("diagnose", "propose"):
        scenario_root = root / name
        scenario_root.mkdir(parents=True)
        arguments, collector = scenario_named(name).build(scenario_root)
        run = run_cli(
            arguments,
            monkeypatch,
            collector=collector,
            command_id=COMMAND_ID,
            clock=lambda: datetime(2026, 1, 1, tzinfo=UTC),
        )
        assert run.exit_code == 0, run.stderr
    return sorted(root.rglob("*.json"))


def tampered(path: Path, field: str, blank: bool) -> dict[str, object]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if blank:
        document[field] = ""
    else:
        del document[field]
    return document


@pytest.mark.parametrize("blank", [True, False], ids=["blank", "missing"])
def test_every_store_rejects_a_record_without_its_stored_digest(
    tmp_path: Path,
    monkeypatch,
    blank: bool,
) -> None:
    checked: set[type[BaseModel]] = set()
    paths = stored_records(tmp_path / "runs", monkeypatch)
    stores = store_directories(tmp_path / "runs", paths)
    for path in paths:
        relative = path.relative_to(tmp_path / "runs").parts[1:-1]
        directory = "/".join(relative)
        if directory == "commands":
            model, fields = CommandEnvelope, SELF_DIGESTS[CommandEnvelope]
        elif directory in stores:
            store_type = stores[directory]
            model = store_type.record_type
            fields = SELF_DIGESTS[model]
        else:
            continue
        for field in fields:
            target = tmp_path / "tampered" / directory / field
            target.mkdir(parents=True, exist_ok=True)
            (target / path.name).write_text(
                json.dumps(tampered(path, field, blank)),
                encoding="utf-8",
            )
            if directory == "commands":
                with pytest.raises(CommandRecordUnreadable):
                    JsonCommandStore(target).load(KEY)
            else:
                with pytest.raises(ValueError, match=f"no stored {field}"):
                    store_type(target).load(UUID(path.stem))
            (target / path.name).unlink()
        checked.add(model)

    assert checked == {store.record_type for store in STORE_TYPES} | {
        CommandEnvelope
    }
    assert {store.record_type for store in STORE_TYPES} | {CommandEnvelope} <= (
        set(SELF_DIGESTS)
    ), "every stored record type needs a self-digest"


def test_command_store_rejects_a_record_stored_under_another_key(
    tmp_path: Path,
) -> None:
    store = JsonCommandStore(tmp_path)
    [path] = stored_command(tmp_path, "key-a")
    other = tmp_path / f"{hashlib.sha256(b'key-b').hexdigest()}.json"
    other.write_bytes(path.read_bytes())

    with pytest.raises(
        CommandRecordUnreadable,
        match="idempotency key record is unreadable: key-b",
    ):
        store.load("key-b")


def stored_command(root: Path, key: str) -> list[Path]:
    JsonCommandStore(root).save(
        make_command(idempotency_key=key, status=CommandState.SUCCEEDED)
    )
    return sorted(root.glob("*.json"))


def test_cli_rejects_a_record_stored_under_another_key_before_collection(
    tmp_path: Path,
    monkeypatch,
) -> None:
    scenario = scenario_named("inventory-audited")
    arguments, collector = scenario.build(tmp_path)
    [path] = stored_command(tmp_path / "commands", "key-a")
    key_path = tmp_path / "commands" / (
        f"{hashlib.sha256(KEY.encode()).hexdigest()}.json"
    )
    key_path.write_bytes(path.read_bytes())

    run = run_cli(
        arguments,
        monkeypatch,
        collector=collector,
        command_id=COMMAND_ID,
        clock=lambda: datetime(2026, 1, 1, tzinfo=UTC),
    )

    assert run.exit_code == 2
    assert run.stderr == f"aura: idempotency key record is unreadable: {KEY}\n"
    assert run.writes == []


def test_every_store_refuses_a_record_whose_digest_is_stale(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runs = tmp_path / "runs"
    paths = stored_records(runs, monkeypatch)
    stores = store_directories(runs, paths)
    for directory in ["commands", *stores]:
        (path, *_) = [
            path
            for path in paths
            if "/".join(path.relative_to(runs).parts[1:-1]) == directory
        ]
        target = tmp_path / "saved" / directory
        if directory == "commands":
            store, model = JsonCommandStore(target), CommandEnvelope
        else:
            store = stores[directory](target)
            model = store.record_type
        record = model.model_validate_json(path.read_text(encoding="utf-8"))
        # model_copy skips validation, so the copy keeps a stale digest.
        stale = record.model_copy(
            update={field: "0" * 64 for field in SELF_DIGESTS[model]}
        )

        with pytest.raises(ValueError):
            store.save(stale)

        assert not target.exists() or list(target.iterdir()) == []


def test_every_execution_store_table_stores_a_self_digest() -> None:
    for table in TABLES.values():
        assert table.digest in SELF_DIGESTS[table.model], table.name
