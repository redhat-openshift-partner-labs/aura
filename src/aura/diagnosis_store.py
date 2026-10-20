from __future__ import annotations

from pathlib import Path
from typing import final
from uuid import UUID

from aura.diagnosis import DiagnosisRecord
from aura.json_store import save_private_json


class DiagnosisAlreadyStored(Exception):
    """Raised when saving a diagnosis that already exists."""


class DiagnosisNotFound(Exception):
    """Raised when loading a diagnosis that does not exist."""


@final
class JsonDiagnosisStore:
    """Stores immutable diagnoses as JSON files keyed by CWA snapshot."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def save(self, record: DiagnosisRecord) -> Path:
        return save_private_json(
            self.root,
            self._path(record.cwa_snapshot_id),
            record,
            lambda message: DiagnosisAlreadyStored(message),
            f"diagnosis already stored: {record.cwa_snapshot_id}"
        )

    def load(self, cwa_snapshot_id: UUID) -> DiagnosisRecord:
        path = self._path(cwa_snapshot_id)
        try:
            value = path.read_text(encoding="utf-8")
        except FileNotFoundError as error:
            raise DiagnosisNotFound(
                f"diagnosis not found: {cwa_snapshot_id}"
            ) from error
        return DiagnosisRecord.model_validate_json(value)

    def _path(self, cwa_snapshot_id: UUID) -> Path:
        return self.root / f"{cwa_snapshot_id}.json"


__all__ = [
    "DiagnosisAlreadyStored",
    "DiagnosisNotFound",
    "JsonDiagnosisStore",
]
