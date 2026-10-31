"""Contracts for Aura execution adapters (Phase 3 task 3.1).

Only the guard calls an adapter's write method, after the `dispatching`
record is stored (I10). A request names the approved proposal by both its
digest and its action digest.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


_DIGEST = r"^[0-9a-f]{64}$"


class AdapterName(StrEnum):
    """The adapters in Phase 3 scope."""

    GIT_HANDOFF = "git_handoff"
    GIT_PR = "git_pr"
    TEKTON = "tekton"
    # Writes nothing outside Aura; tests inject it into `execute`.
    RECORDING = "recording"


class AdapterRequest(BaseModel):
    """Input to an adapter's write method for one approved proposal."""

    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    command_id: UUID
    execution_id: UUID
    proposal_id: UUID
    proposal_digest: str = Field(pattern=_DIGEST)
    action_digest: str = Field(pattern=_DIGEST)
    adapter: AdapterName
    input: dict[str, Any]


class AdapterResult(BaseModel):
    """Output of an adapter's write method."""

    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    execution_id: UUID
    adapter: AdapterName
    success: bool
    external_result: dict[str, str] = Field(default_factory=dict)
    error: str | None = None

    @model_validator(mode="after")
    def validate_error(self) -> AdapterResult:
        if self.success and self.error is not None:
            raise ValueError("successful adapter result must not set error")
        if not self.success and (self.error is None or not self.error.strip()):
            raise ValueError("failed adapter result must set error")
        if self.success and not self.external_result:
            raise ValueError("successful adapter result must name its effect")
        return self


class ExecutionAdapter(Protocol):
    """Implemented by each adapter in `aura.execution`."""

    @property
    def name(self) -> AdapterName: ...

    @property
    def version(self) -> str: ...

    # What the adapter writes to, such as the Kubernetes API or a Git fork;
    # the guard refuses one that targets the fleet EventListener (G10).
    @property
    def target(self) -> str: ...

    # A read-only check that the adapter can reach its system: None when it
    # can, else the problem (G12).
    def preflight(self) -> str | None: ...

    def write(self, request: AdapterRequest) -> AdapterResult: ...


class AdapterOutcomeUnknown(Exception):
    """Raised by a write whose effect may exist; its message names no
    secret value, so it is stored whole."""


__all__ = [
    "AdapterName",
    "AdapterOutcomeUnknown",
    "AdapterRequest",
    "AdapterResult",
    "ExecutionAdapter",
]
