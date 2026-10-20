from __future__ import annotations

from pathlib import Path
from typing import final
from uuid import UUID

from aura.contracts import AuditEvent
from aura.json_store import save_private_json


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
        return save_private_json(
            self.root,
            self._path(event.event_id),
            event,
            lambda message: AuditEventAlreadyStored(message),
            f"audit event already stored: {event.event_id}"
        )

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
