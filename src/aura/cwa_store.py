from __future__ import annotations

from typing import final

from aura.cwa import CWASnapshot, CWATrace
from aura.json_store import JsonRecordStore


class CWASnapshotAlreadyStored(Exception):
    """Raised when saving a CWA snapshot that already exists."""


class CWASnapshotNotFound(Exception):
    """Raised when loading a CWA snapshot that does not exist."""


class CWATraceAlreadyStored(Exception):
    """Raised when saving a CWA trace that already exists."""


class CWATraceNotFound(Exception):
    """Raised when loading a CWA trace that does not exist."""


@final
class JsonCWASnapshotStore(JsonRecordStore[CWASnapshot]):
    """Stores immutable CWA snapshots as JSON files."""

    record_type = CWASnapshot
    record_label = "CWA snapshot"
    id_field = "snapshot_id"
    already_stored = CWASnapshotAlreadyStored
    not_found = CWASnapshotNotFound


@final
class JsonCWATraceStore(JsonRecordStore[CWATrace]):
    """Stores immutable CWA traces as JSON files."""

    record_type = CWATrace
    record_label = "CWA trace"
    id_field = "trace_id"
    already_stored = CWATraceAlreadyStored
    not_found = CWATraceNotFound


__all__ = [
    "CWASnapshotAlreadyStored",
    "CWASnapshotNotFound",
    "CWATraceAlreadyStored",
    "CWATraceNotFound",
    "JsonCWASnapshotStore",
    "JsonCWATraceStore",
]
