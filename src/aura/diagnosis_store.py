from __future__ import annotations

from pathlib import Path
from typing import final
from uuid import UUID

from aura.diagnosis import DiagnosisRecord


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
        self.root.mkdir(parents=True, exist_ok=True)
        path = self._path(record.cwa_snapshot_id)
        try:
            with path.open("x", encoding="utf-8") as stream:
                stream.write(record.model_dump_json(indent=2))
        except FileExistsError as error:
            raise DiagnosisAlreadyStored(
                f"diagnosis already stored: {record.cwa_snapshot_id}"
            ) from error
        return path

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
