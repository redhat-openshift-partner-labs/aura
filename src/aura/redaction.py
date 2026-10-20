"""Secret redaction for evidence before model assembly."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from aura.contracts import EvidenceSnapshot


DEFAULT_SECRET_FIELDS = frozenset(
    {
        "api_key",
        "authorization",
        "client_secret",
        "cookie",
        "kubeconfig",
        "password",
        "private_key",
        "pull_secret",
        "secret",
        "ssh_private_key",
        "token",
    }
)


@dataclass(slots=True)
class RedactionResult:
    """A redacted value and the fields that were removed."""

    value: Any
    redacted_fields: list[str] = field(default_factory=list)


@dataclass(slots=True)
class RedactionPolicy:
    """Policy controlling which object keys are treated as secrets."""

    secret_fields: frozenset[str] = DEFAULT_SECRET_FIELDS

    def __post_init__(self) -> None:
        self.secret_fields = frozenset(
            name.lower() for name in self.secret_fields
        )

    def redact(self, value: Any) -> RedactionResult:
        redacted_fields: list[str] = []
        redacted_value = self._redact(value, [], redacted_fields)
        return RedactionResult(
            value=redacted_value,
            redacted_fields=sorted(redacted_fields),
        )

    def _redact(
        self,
        value: Any,
        path: list[str],
        redacted_fields: list[str],
    ) -> Any:
        if isinstance(value, Mapping):
            result: dict[str, Any] = {}
            for key, item in value.items():
                key_text = str(key)
                child_path = [*path, key_text]
                if key_text.lower() in self.secret_fields:
                    redacted_fields.append(self._path(child_path))
                    result[key_text] = "[REDACTED]"
                else:
                    result[key_text] = self._redact(
                        item, child_path, redacted_fields
                    )
            return result

        if isinstance(value, list):
            return [
                self._redact(item, [*path, f"[{index}]"], redacted_fields)
                for index, item in enumerate(value)
            ]

        return value

    @staticmethod
    def _path(parts: list[str]) -> str:
        if not parts:
            return ""
        result = parts[0]
        for part in parts[1:]:
            if part.startswith("["):
                result += part
            else:
                result += f".{part}"
        return result


def redact(value: Any) -> RedactionResult:
    """Redact default secret fields from a JSON-like value."""

    return RedactionPolicy().redact(value)


def redact_snapshot(snapshot: EvidenceSnapshot) -> EvidenceSnapshot:
    """Return a snapshot with secrets removed and its digest recomputed."""

    payload = snapshot.model_dump(mode="json")
    result = redact(payload)
    payload = result.value
    payload["redactions"] = sorted(
        {*snapshot.redactions, *result.redacted_fields}
    )
    payload["snapshot_digest"] = ""
    return EvidenceSnapshot.model_validate(payload)


__all__ = [
    "DEFAULT_SECRET_FIELDS",
    "RedactionPolicy",
    "RedactionResult",
    "redact",
    "redact_snapshot",
]

