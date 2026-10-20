from __future__ import annotations

from pathlib import Path
from typing import final
from uuid import UUID

from aura.contracts import EvidenceSnapshot
from aura.json_store import save_private_json


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
        return save_private_json(
            self.root,
            self._path(snapshot.snapshot_id),
            snapshot,
            lambda message: EvidenceSnapshotAlreadyStored(message),
            f"evidence snapshot already stored: {snapshot.snapshot_id}"
        )

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
