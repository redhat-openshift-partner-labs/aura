"""Every store verifies a record's own digest on load (I9).

Model validation recomputes a blank digest, so a store must require the
stored value before validating; otherwise an edited record with its
digest removed loads as genuine.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import BaseModel

from aura.audit_store import JsonAuditEventStore
from aura.command_store import (
    CommandRecordUnreadable,
    JsonCommandStore,
)
from aura.contracts import (
    AuditEvent,
    CommandEnvelope,
    CommandState,
    EvidenceItem,
    EvidenceSnapshot,
)
from aura.cwa import CWASnapshot, CWATrace
from aura.cwa_store import JsonCWASnapshotStore, JsonCWATraceStore
from aura.diagnosis import DiagnosisRecord, DiagnosisResponse
from aura.diagnosis_store import JsonDiagnosisStore
from aura.evidence_store import JsonEvidenceSnapshotStore
from aura.proposal_store import JsonProposalStore
from aura.proposals import Proposal, ScaffoldRun
from aura.scaffold import ScaffoldRequest
from failure_injection import run_cli
from test_failure_matrix import COMMAND_ID, KEY, scenario_named
from test_inventory_service import make_command


SELF_DIGESTS = {
    CommandEnvelope: ("request_digest", "record_digest"),
    EvidenceSnapshot: ("snapshot_digest",),
    AuditEvent: ("event_digest",),
    CWASnapshot: ("snapshot_digest",),
    DiagnosisRecord: ("response_digest",),
    Proposal: ("proposal_digest",),
}
STORES = {
    "snapshot": JsonEvidenceSnapshotStore,
    "audit": JsonAuditEventStore,
    "cwa/snapshots": JsonCWASnapshotStore,
    "diagnosis": JsonDiagnosisStore,
    "proposal": JsonProposalStore,
}
CONTRACTS = (
    AuditEvent,
    CommandEnvelope,
    CWASnapshot,
    CWATrace,
    DiagnosisRecord,
    DiagnosisResponse,
    EvidenceItem,
    EvidenceSnapshot,
    Proposal,
    ScaffoldRequest,
    ScaffoldRun,
)


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
    for path in stored_records(tmp_path / "runs", monkeypatch):
        relative = path.relative_to(tmp_path / "runs").parts[1:-1]
        directory = "/".join(relative)
        if directory == "commands":
            model, fields = CommandEnvelope, SELF_DIGESTS[CommandEnvelope]
        elif directory in STORES:
            store_type = STORES[directory]
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

    assert checked == set(SELF_DIGESTS)


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
