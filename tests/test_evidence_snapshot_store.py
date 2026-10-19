from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest

from aura.contracts import EvidenceSnapshot
from aura.evidence_store import (
    EvidenceSnapshotAlreadyStored,
    EvidenceSnapshotNotFound,
    JsonEvidenceSnapshotStore,
)


def make_snapshot() -> EvidenceSnapshot:
    return EvidenceSnapshot(
        snapshot_id=UUID("00000000-0000-0000-0000-000000000050"),
        command_id=UUID("00000000-0000-0000-0000-000000000001"),
        collector="git_inventory",
        collector_version="0.1.0",
        collected_at=datetime(2026, 1, 1, tzinfo=UTC),
        source="fleet-clusters",
        source_version="0123456789abcdef0123456789abcdef01234567",
        items=[],
        redactions=[],
    )


def test_json_evidence_snapshot_store_saves_and_loads(tmp_path) -> None:
    store = JsonEvidenceSnapshotStore(root=tmp_path / "snapshots")
    snapshot = make_snapshot()

    path = store.save(snapshot)
    loaded = store.load(snapshot.snapshot_id)

    assert path == (
        tmp_path
        / "snapshots"
        / "00000000-0000-0000-0000-000000000050.json"
    )
    assert loaded == snapshot
    assert path.read_text(encoding="utf-8") == snapshot.model_dump_json(
        indent=2
    )


def test_json_evidence_snapshot_store_rejects_duplicate(tmp_path) -> None:
    store = JsonEvidenceSnapshotStore(root=tmp_path)
    snapshot = make_snapshot()
    store.save(snapshot)

    with pytest.raises(EvidenceSnapshotAlreadyStored):
        store.save(snapshot)


def test_json_evidence_snapshot_store_rejects_missing(tmp_path) -> None:
    store = JsonEvidenceSnapshotStore(root=tmp_path)

    with pytest.raises(EvidenceSnapshotNotFound):
        store.load(UUID("00000000-0000-0000-0000-000000000099"))
