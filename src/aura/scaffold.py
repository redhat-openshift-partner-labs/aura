"""Request, result, and runner types for scaffold proposals."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Protocol

from pydantic import BaseModel, ConfigDict


class ScaffoldRequest(BaseModel):
    """What the operator asked the pinned scaffold to generate."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    cloud: str
    cluster_name: str
    region: str
    tier: str
    options: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class ScaffoldResult:
    """What one scaffold run wrote, keyed by workspace-relative path."""

    exit_code: int
    stdout: str
    stderr: str
    files: dict[str, tuple[str, str]] = field(default_factory=dict)


INFRA_PREFIX_LENGTH = 21


class ScaffoldRunError(Exception):
    """Raised when the scaffold cannot be run at all."""


class ScaffoldCheckoutError(Exception):
    """Raised when the scaffold checkout cannot be read."""


class ScaffoldRunner(Protocol):
    def run(
        self,
        *,
        scaffold_commit: str,
        base_commit: str,
        arguments: list[str],
    ) -> ScaffoldResult:
        ...


def infra_prefix(cluster_name: str) -> str:
    """Return the installer infra ID prefix for a cluster name.

    Scoped IAM policies match `<prefix>-?????`, so two clusters with the
    same prefix can act on each other's AWS resources. Mirrors the rule
    vendored in fleet and in fleet-clusters scripts/check_new_overlays.py.
    """

    prefix = re.sub(r"-+", "-", re.sub(r"[^A-Za-z0-9-]", "-", cluster_name))
    return prefix[:INFRA_PREFIX_LENGTH].rstrip("-")


__all__ = [
    "INFRA_PREFIX_LENGTH",
    "ScaffoldCheckoutError",
    "ScaffoldRequest",
    "ScaffoldResult",
    "ScaffoldRunError",
    "ScaffoldRunner",
    "infra_prefix",
]
