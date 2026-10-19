from __future__ import annotations

from pathlib import Path
from typing import final
from uuid import UUID

from aura.contracts import AuditEvent


class AuditEventAlreadyStored(Exception):
    """Raised when saving an audit event that already exists."""


class AuditEventNotFound(Exception):
    """Raised when loading an audit event that does not exist."""


@final
class JsonAuditEventStore:
    """Stores immutable audit events as JSON files."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def save(self, event: AuditEvent) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self._path(event.event_id)
        try:
            with path.open("x", encoding="utf-8") as stream:
                stream.write(event.model_dump_json(indent=2))
        except FileExistsError as error:
            raise AuditEventAlreadyStored(
                f"audit event already stored: {event.event_id}"
            ) from error
        return path

    def load(self, event_id: UUID) -> AuditEvent:
        path = self._path(event_id)
        try:
            value = path.read_text(encoding="utf-8")
        except FileNotFoundError as error:
            raise AuditEventNotFound(
                f"audit event not found: {event_id}"
            ) from error
        return AuditEvent.model_validate_json(value)

    def _path(self, event_id: UUID) -> Path:
        return self.root / f"{event_id}.json"


__all__ = [
    "AuditEventAlreadyStored",
    "AuditEventNotFound",
    "JsonAuditEventStore",
]
