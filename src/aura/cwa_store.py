from __future__ import annotations

from pathlib import Path
from typing import final
from uuid import UUID

from aura.cwa import CWASnapshot, CWATrace


class CWASnapshotAlreadyStored(Exception):
    """Raised when saving a CWA snapshot that already exists."""


class CWASnapshotNotFound(Exception):
    """Raised when loading a CWA snapshot that does not exist."""


class CWATraceAlreadyStored(Exception):
    """Raised when saving a CWA trace that already exists."""


class CWATraceNotFound(Exception):
    """Raised when loading a CWA trace that does not exist."""


@final
class JsonCWASnapshotStore:
    """Stores immutable CWA snapshots as JSON files."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def save(self, snapshot: CWASnapshot) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self._path(snapshot.snapshot_id)
        try:
            with path.open("x", encoding="utf-8") as stream:
                stream.write(snapshot.model_dump_json(indent=2))
        except FileExistsError as error:
            raise CWASnapshotAlreadyStored(
                f"CWA snapshot already stored: {snapshot.snapshot_id}"
            ) from error
        return path

    def load(self, snapshot_id: UUID) -> CWASnapshot:
        path = self._path(snapshot_id)
        try:
            value = path.read_text(encoding="utf-8")
        except FileNotFoundError as error:
            raise CWASnapshotNotFound(
                f"CWA snapshot not found: {snapshot_id}"
            ) from error
        return CWASnapshot.model_validate_json(value)

    def _path(self, snapshot_id: UUID) -> Path:
        return self.root / f"{snapshot_id}.json"


@final
class JsonCWATraceStore:
    """Stores immutable CWA traces as JSON files."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def save(self, trace: CWATrace) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self._path(trace.trace_id)
        try:
            with path.open("x", encoding="utf-8") as stream:
                stream.write(trace.model_dump_json(indent=2))
        except FileExistsError as error:
            raise CWATraceAlreadyStored(
                f"CWA trace already stored: {trace.trace_id}"
            ) from error
        return path

    def load(self, trace_id: UUID) -> CWATrace:
        path = self._path(trace_id)
        try:
            value = path.read_text(encoding="utf-8")
        except FileNotFoundError as error:
            raise CWATraceNotFound(
                f"CWA trace not found: {trace_id}"
            ) from error
        return CWATrace.model_validate_json(value)

    def _path(self, trace_id: UUID) -> Path:
        return self.root / f"{trace_id}.json"


__all__ = [
    "CWASnapshotAlreadyStored",
    "CWASnapshotNotFound",
    "CWATraceAlreadyStored",
    "CWATraceNotFound",
    "JsonCWASnapshotStore",
    "JsonCWATraceStore",
]
