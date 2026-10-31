"""Secret redaction for evidence before model assembly."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from aura.contracts import EvidenceSnapshot


DEFAULT_SECRET_FIELDS = frozenset(
    {
        "access_token",
        "api_key",
        "authorization",
        "aws_secret_access_key",
        "bearer_token",
        "client_secret",
        "cookie",
        "kubeconfig",
        "password",
        "private_key",
        "pull_secret",
        "refresh_token",
        "secret",
        "secret_access_key",
        "ssh_private_key",
        "token",
    }
)


PARAMETER_SECRET_TERMS = (
    "apikey",
    "credential",
    "kubeconfig",
    "passwd",
    "password",
    "privatekey",
    "secret",
    "token",
)

NON_SECRET_PARAMETERS = frozenset(
    {
        "htpasswd-provider-name",
        "keycloak-admin-secret",
        "spoke-kubeconfig",
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
            _normalized_field(name) for name in self.secret_fields
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
                if _normalized_field(key_text) in self.secret_fields:
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


def _normalized_field(name: str) -> str:
    """Fold case and separators so `pullSecret` matches `pull_secret`."""

    return re.sub(r"[^a-z0-9]", "", name.lower())


_SECRET_TEXT = re.compile(
    r"""(?ix)
    ( ["']? [\w.-]* (?:password|passwd|secret|token|apikey|api_key|credential|
      private_?key|kubeconfig|authorization|cookie) [\w.-]* ["']? \s* [:=] \s* )
    ( (?:bearer\s+)? (?: "[^"]*" | '[^']*' | [^\s,;}\]]+ ) )
    | (bearer \s+) [^\s,;]+
    """
)


def redact_text(text: str) -> str:
    """Mask the value of every secret-named `name: value` or `name=value`
    pair, and every bearer token, in free text such as an error message."""

    def masked(match: re.Match[str]) -> str:
        prefix = match.group(1) if match.group(1) is not None else match.group(3)
        return f"{prefix}[REDACTED]"

    return _SECRET_TEXT.sub(masked, text)


def redact(value: Any) -> RedactionResult:
    """Redact default secret fields from a JSON-like value."""

    return RedactionPolicy().redact(value)


def redact_parameters(parameters: Mapping[str, Any]) -> RedactionResult:
    """Redact parameter values whose names contain a secret term.

    Pipeline parameters carry values under generic names, so field-name
    matching alone cannot protect them. Known fleet parameters that hold
    a Secret name or a file path are listed in NON_SECRET_PARAMETERS.
    """

    redacted: dict[str, Any] = {}
    redacted_fields: list[str] = []
    for name, value in parameters.items():
        normalized = _normalized_field(name)
        if name not in NON_SECRET_PARAMETERS and any(
            term in normalized for term in PARAMETER_SECRET_TERMS
        ):
            redacted[name] = "[REDACTED]"
            redacted_fields.append(name)
        else:
            redacted[name] = value
    return RedactionResult(
        value=redacted,
        redacted_fields=sorted(redacted_fields),
    )


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
    "NON_SECRET_PARAMETERS",
    "PARAMETER_SECRET_TERMS",
    "RedactionPolicy",
    "RedactionResult",
    "redact",
    "redact_parameters",
    "redact_snapshot",
]

