from __future__ import annotations

from typing import final

from aura.diagnosis import DiagnosisRecord
from aura.json_store import JsonRecordStore


class DiagnosisAlreadyStored(Exception):
    """Raised when saving a diagnosis that already exists."""


class DiagnosisNotFound(Exception):
    """Raised when loading a diagnosis that does not exist."""


@final
class JsonDiagnosisStore(JsonRecordStore[DiagnosisRecord]):
    """Stores immutable diagnoses as JSON files keyed by CWA snapshot."""

    record_type = DiagnosisRecord
    record_label = "diagnosis"
    id_field = "cwa_snapshot_id"
    already_stored = DiagnosisAlreadyStored
    not_found = DiagnosisNotFound


__all__ = [
    "DiagnosisAlreadyStored",
    "DiagnosisNotFound",
    "JsonDiagnosisStore",
]
