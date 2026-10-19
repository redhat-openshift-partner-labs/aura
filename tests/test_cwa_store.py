from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest

from aura.cwa import CWASlot, CWASlotName, CWASnapshot, CWATrace
from aura.cwa_store import (
    CWASnapshotAlreadyStored,
    CWASnapshotNotFound,
    CWATraceAlreadyStored,
    CWATraceNotFound,
    JsonCWASnapshotStore,
    JsonCWATraceStore,
)


def make_snapshot() -> CWASnapshot:
    slots = [
        CWASlot(name=name, content={})
        for name in CWASlotName
    ]
    return CWASnapshot(
        snapshot_id=UUID("00000000-0000-0000-0000-000000000060"),
        command_id=UUID("00000000-0000-0000-0000-000000000001"),
        evidence_snapshot_id=UUID("00000000-0000-0000-0000-000000000050"),
        evidence_snapshot_digest="a" * 64,
        assembler_version="0.1.0",
        assembled_at=datetime(2026, 1, 1, tzinfo=UTC),
        slots=slots,
    )


def make_trace() -> CWATrace:
    return CWATrace(
        trace_id=UUID("00000000-0000-0000-0000-000000000070"),
        command_id=UUID("00000000-0000-0000-0000-000000000001"),
        cwa_snapshot_id=UUID("00000000-0000-0000-0000-000000000060"),
        cwa_snapshot_digest="b" * 64,
        evidence_snapshot_id=UUID("00000000-0000-0000-0000-000000000050"),
        evidence_snapshot_digest="a" * 64,
        audit_event_id=UUID("00000000-0000-0000-0000-000000000020"),
        assembler_version="0.1.0",
        assembled_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def test_json_cwa_snapshot_store_saves_and_loads(tmp_path) -> None:
    store = JsonCWASnapshotStore(root=tmp_path / "cwa-snapshots")
    snapshot = make_snapshot()

    path = store.save(snapshot)
    loaded = store.load(snapshot.snapshot_id)

    assert path == (
        tmp_path
        / "cwa-snapshots"
        / "00000000-0000-0000-0000-000000000060.json"
    )
    assert loaded == snapshot
    assert path.read_text(encoding="utf-8") == snapshot.model_dump_json(
        indent=2
    )


def test_json_cwa_snapshot_store_rejects_duplicate(tmp_path) -> None:
    store = JsonCWASnapshotStore(root=tmp_path)
    snapshot = make_snapshot()
    store.save(snapshot)

    with pytest.raises(CWASnapshotAlreadyStored):
        store.save(snapshot)


def test_json_cwa_snapshot_store_rejects_missing(tmp_path) -> None:
    store = JsonCWASnapshotStore(root=tmp_path)

    with pytest.raises(CWASnapshotNotFound):
        store.load(UUID("00000000-0000-0000-0000-000000000099"))


def test_json_cwa_trace_store_saves_and_loads(tmp_path) -> None:
    store = JsonCWATraceStore(root=tmp_path / "cwa-traces")
    trace = make_trace()

    path = store.save(trace)
    loaded = store.load(trace.trace_id)

    assert path == (
        tmp_path
        / "cwa-traces"
        / "00000000-0000-0000-0000-000000000070.json"
    )
    assert loaded == trace
    assert path.read_text(encoding="utf-8") == trace.model_dump_json(
        indent=2
    )


def test_json_cwa_trace_store_rejects_duplicate(tmp_path) -> None:
    store = JsonCWATraceStore(root=tmp_path)
    trace = make_trace()
    store.save(trace)

    with pytest.raises(CWATraceAlreadyStored):
        store.save(trace)


def test_json_cwa_trace_store_rejects_missing(tmp_path) -> None:
    store = JsonCWATraceStore(root=tmp_path)

    with pytest.raises(CWATraceNotFound):
        store.load(UUID("00000000-0000-0000-0000-000000000099"))
