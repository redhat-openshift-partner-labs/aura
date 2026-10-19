from __future__ import annotations

from pathlib import Path
from typing import final
from uuid import UUID

from aura.contracts import EvidenceSnapshot


class EvidenceSnapshotAlreadyStored(Exception):
    """Raised when saving a snapshot that already exists."""


class EvidenceSnapshotNotFound(Exception):
    """Raised when loading a snapshot that does not exist."""


@final
class JsonEvidenceSnapshotStore:
    """Stores immutable evidence snapshots as JSON files."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def save(self, snapshot: EvidenceSnapshot) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self._path(snapshot.snapshot_id)
        try:
            with path.open("x", encoding="utf-8") as stream:
                stream.write(snapshot.model_dump_json(indent=2))
        except FileExistsError as error:
            raise EvidenceSnapshotAlreadyStored(
                f"evidence snapshot already stored: {snapshot.snapshot_id}"
            ) from error
        return path

    def load(self, snapshot_id: UUID) -> EvidenceSnapshot:
        path = self._path(snapshot_id)
        try:
            value = path.read_text(encoding="utf-8")
        except FileNotFoundError as error:
            raise EvidenceSnapshotNotFound(
                f"evidence snapshot not found: {snapshot_id}"
            ) from error
        return EvidenceSnapshot.model_validate_json(value)

    def _path(self, snapshot_id: UUID) -> Path:
        return self.root / f"{snapshot_id}.json"


__all__ = [
    "EvidenceSnapshotAlreadyStored",
    "EvidenceSnapshotNotFound",
    "JsonEvidenceSnapshotStore",
]
