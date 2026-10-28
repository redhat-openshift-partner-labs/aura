"""Cross-record references are verified when a record is loaded with its
provenance (I9, Phase 3 task 1.6)."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest

from pydantic import BaseModel

from aura.audit_store import JsonAuditEventStore
from aura.contracts import AuditEvent, AuditEventType, EvidenceSnapshot
from aura.cwa import CWASnapshot, CWATrace
from aura.diagnosis import DiagnosisRecord
from aura.proposals import Proposal
from aura.provenance import (
    ProvenanceError,
    load_diagnosis_provenance,
    load_proposal_provenance,
)
from failure_injection import run_cli
from test_failure_matrix import COMMAND_ID, scenario_named


OTHER_DIGEST = "1" * 64


def stored_run(root: Path, monkeypatch, name: str) -> Path:
    arguments, collector = scenario_named(name).build(root)
    run = run_cli(
        arguments,
        monkeypatch,
        collector=collector,
        command_id=COMMAND_ID,
        clock=lambda: datetime(2026, 1, 1, tzinfo=UTC),
    )
    assert run.exit_code == 0, run.stderr
    return root


def only(directory: Path) -> Path:
    (path,) = directory.glob("*.json")
    return path


def proposal_provenance(root: Path):
    return load_proposal_provenance(
        UUID(only(root / "proposal").stem),
        proposal_dir=root / "proposal",
        cwa_dir=root / "cwa",
        snapshot_dir=root / "snapshot",
        audit_dir=root / "audit",
    )


def diagnosis_provenance(root: Path):
    return load_diagnosis_provenance(
        UUID(only(root / "diagnosis").stem),
        diagnosis_dir=root / "diagnosis",
        cwa_dir=root / "cwa",
        snapshot_dir=root / "snapshot",
        audit_dir=root / "audit",
    )


def rewrite(path: Path, model: type[BaseModel], digest: str | None,
            **changes: Any) -> BaseModel:
    """Change record fields and recompute its own digest, so only a
    cross-record check can catch the change."""

    document = {**json.loads(path.read_text(encoding="utf-8")), **changes}
    if digest is not None:
        document[digest] = ""
    record = model.model_validate(document)
    path.write_text(record.model_dump_json(), encoding="utf-8")
    return record


def rewrite_trace(root: Path, **changes: Any) -> None:
    rewrite(only(root / "cwa" / "traces"), CWATrace, "trace_digest", **changes)


def point_trace_at_another_commands_event(
    root: Path,
    *,
    move_trace: bool = False,
) -> None:
    command_id = uuid4()
    event = AuditEvent(
        event_id=uuid4(),
        command_id=command_id,
        event_type=AuditEventType.COMMAND_RECEIVED,
        occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
        actor="other",
        data={},
    )
    JsonAuditEventStore(root / "audit").save(event)
    changes = {"audit_event_id": str(event.event_id)}
    if move_trace:
        changes["command_id"] = str(command_id)
    rewrite_trace(root, **changes)


def change_cwa_evidence_digest(root: Path) -> None:
    snapshot = rewrite(
        only(root / "cwa" / "snapshots"),
        CWASnapshot,
        "snapshot_digest",
        evidence_snapshot_digest=OTHER_DIGEST,
    )
    rewrite_trace(root, cwa_snapshot_digest=snapshot.snapshot_digest)


def change_stored_evidence(root: Path) -> None:
    path = only(root / "snapshot")
    snapshot = json.loads(path.read_text(encoding="utf-8"))
    rewrite(
        path,
        EvidenceSnapshot,
        "snapshot_digest",
        collected_at="2030-01-01T00:00:00Z",
    )
    assert snapshot["collected_at"] != "2030-01-01T00:00:00Z"


def remove(directory: str) -> Callable[[Path], None]:
    return lambda root: only(root / directory).unlink()


def test_proposal_loads_with_its_provenance(tmp_path, monkeypatch) -> None:
    root = stored_run(tmp_path, monkeypatch, "propose")

    provenance = proposal_provenance(root)

    assert provenance.trace.trace_id == provenance.proposal.cwa_trace_id
    assert provenance.trace.response_digest == (
        provenance.proposal.proposal_digest
    )
    assert provenance.cwa_snapshot.snapshot_id == (
        provenance.proposal.cwa_snapshot_id
    )
    assert provenance.evidence_snapshot.snapshot_digest == (
        provenance.proposal.evidence_snapshot_digest
    )
    assert provenance.audit_event.event_id == provenance.trace.audit_event_id


def test_diagnosis_loads_with_its_provenance(tmp_path, monkeypatch) -> None:
    root = stored_run(tmp_path, monkeypatch, "diagnose")

    provenance = diagnosis_provenance(root)

    assert provenance.trace.response_digest == (
        provenance.diagnosis.response_digest
    )
    assert provenance.cwa_snapshot.snapshot_digest == (
        provenance.diagnosis.cwa_snapshot_digest
    )


@pytest.mark.parametrize(
    ("tamper", "message"),
    [
        (lambda root: rewrite_trace(root, response_digest=OTHER_DIGEST),
         "response_digest"),
        (lambda root: rewrite_trace(root, cwa_snapshot_digest=OTHER_DIGEST),
         "cwa_snapshot_digest"),
        (lambda root: rewrite_trace(root, evidence_snapshot_digest=OTHER_DIGEST),
         "evidence_snapshot_digest"),
        (lambda root: rewrite_trace(root, evidence_snapshot_id=str(uuid4())),
         "evidence_snapshot_id"),
        (lambda root: rewrite_trace(root, cwa_snapshot_id=str(uuid4())),
         "cwa_snapshot_id"),
        (lambda root: rewrite_trace(root, command_id=str(uuid4())),
         "command_id"),
        (lambda root: rewrite_trace(root, audit_event_id=str(uuid4())),
         "audit event not found"),
        (point_trace_at_another_commands_event, "audit event command_id"),
        (change_stored_evidence, "evidence_snapshot_digest"),
        (lambda root: point_trace_at_another_commands_event(
            root, move_trace=True), "command_id does not match"),
        (change_cwa_evidence_digest, "evidence_snapshot_digest"),
        (remove("cwa/traces"), "CWA trace not found"),
        (remove("cwa/snapshots"), "CWA snapshot not found"),
        (remove("snapshot"), "evidence snapshot not found"),
    ],
    ids=[
        "response-digest",
        "cwa-snapshot-digest",
        "evidence-digest",
        "evidence-id",
        "cwa-snapshot-id",
        "command-id",
        "audit-event",
        "other-command-event",
        "stored-evidence",
        "trace-of-another-command",
        "cwa-evidence-digest",
        "missing-trace",
        "missing-cwa-snapshot",
        "missing-evidence",
    ],
)
@pytest.mark.parametrize("kind", ["propose", "diagnose"])
def test_a_broken_reference_is_refused(
    tmp_path,
    monkeypatch,
    kind,
    tamper,
    message,
) -> None:
    root = stored_run(tmp_path, monkeypatch, kind)
    tamper(root)
    load = proposal_provenance if kind == "propose" else diagnosis_provenance

    with pytest.raises(ProvenanceError, match=message):
        load(root)


def test_a_trace_without_its_digest_is_refused(tmp_path, monkeypatch) -> None:
    root = stored_run(tmp_path, monkeypatch, "propose")
    path = only(root / "cwa" / "traces")
    document = json.loads(path.read_text(encoding="utf-8"))
    del document["trace_digest"]
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ProvenanceError, match="no stored trace_digest"):
        proposal_provenance(root)


def test_trace_digest_detects_a_changed_field() -> None:
    trace = CWATrace(
        trace_id=uuid4(),
        command_id=uuid4(),
        cwa_snapshot_id=uuid4(),
        cwa_snapshot_digest="2" * 64,
        evidence_snapshot_id=uuid4(),
        evidence_snapshot_digest="3" * 64,
        audit_event_id=uuid4(),
        assembler_version="test",
        assembled_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    document = {**trace.model_dump(mode="json"), "assembler_version": "other"}

    assert len(trace.trace_digest) == 64
    with pytest.raises(ValueError, match="trace digest"):
        CWATrace.model_validate(document)
    updated = trace.with_response("p", "m", OTHER_DIGEST)
    assert updated.response_digest == OTHER_DIGEST
    assert updated.trace_digest != trace.trace_digest


def test_a_proposal_whose_evidence_differs_from_its_trace_is_refused(
    tmp_path,
    monkeypatch,
) -> None:
    root = stored_run(tmp_path, monkeypatch, "propose")
    proposal = rewrite(
        only(root / "proposal"),
        Proposal,
        "proposal_digest",
        evidence_snapshot_digest=OTHER_DIGEST,
    )
    rewrite_trace(root, response_digest=proposal.proposal_digest)

    with pytest.raises(ProvenanceError, match="evidence_snapshot_digest"):
        proposal_provenance(root)


def test_a_proposal_whose_evidence_id_differs_from_its_trace_is_refused(
    tmp_path,
    monkeypatch,
) -> None:
    root = stored_run(tmp_path, monkeypatch, "propose")
    proposal = rewrite(
        only(root / "proposal"),
        Proposal,
        "proposal_digest",
        evidence_snapshot_id=str(uuid4()),
    )
    rewrite_trace(root, response_digest=proposal.proposal_digest)

    with pytest.raises(ProvenanceError, match="evidence_snapshot_id"):
        proposal_provenance(root)


def test_a_diagnosis_whose_cwa_digest_differs_is_refused(
    tmp_path,
    monkeypatch,
) -> None:
    root = stored_run(tmp_path, monkeypatch, "diagnose")
    # The diagnosis digest covers only the response, so this field is
    # checked only against the stored CWA snapshot.
    rewrite(
        only(root / "diagnosis"),
        DiagnosisRecord,
        None,
        cwa_snapshot_digest=OTHER_DIGEST,
    )

    with pytest.raises(ProvenanceError, match="cwa_snapshot_digest"):
        diagnosis_provenance(root)
