from __future__ import annotations

from typing import final

from aura.contracts import AuditEvent
from aura.json_store import JsonRecordStore


class AuditEventAlreadyStored(Exception):
    """Raised when saving an audit event that already exists."""


class AuditEventNotFound(Exception):
    """Raised when loading an audit event that does not exist."""


@final
class JsonAuditEventStore(JsonRecordStore[AuditEvent]):
    """Stores immutable audit events as JSON files."""

    record_type = AuditEvent
    record_label = "audit event"
    id_field = "event_id"
    already_stored = AuditEventAlreadyStored
    not_found = AuditEventNotFound


__all__ = [
    "AuditEventAlreadyStored",
    "AuditEventNotFound",
    "JsonAuditEventStore",
]
