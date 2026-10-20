from __future__ import annotations

from typing import final

from aura.contracts import EvidenceSnapshot
from aura.json_store import JsonRecordStore


class EvidenceSnapshotAlreadyStored(Exception):
    """Raised when saving a snapshot that already exists."""


class EvidenceSnapshotNotFound(Exception):
    """Raised when loading a snapshot that does not exist."""


@final
class JsonEvidenceSnapshotStore(JsonRecordStore[EvidenceSnapshot]):
    """Stores immutable evidence snapshots as JSON files."""

    record_type = EvidenceSnapshot
    record_label = "evidence snapshot"
    id_field = "snapshot_id"
    already_stored = EvidenceSnapshotAlreadyStored
    not_found = EvidenceSnapshotNotFound


__all__ = [
    "EvidenceSnapshotAlreadyStored",
    "EvidenceSnapshotNotFound",
    "JsonEvidenceSnapshotStore",
]
