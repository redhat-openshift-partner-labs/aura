"""Contracts for Aura execution adapters."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AdapterName(StrEnum):
    """External systems that can execute an approved Aura proposal."""

    SCAFFOLD = "scaffold"
    GIT_PATCH = "git_patch"
    GIT_PR = "git_pr"
    TEKTON = "tekton"
    NOTIFY = "notify"


class AdapterRequest(BaseModel):
    """Input to an approved execution adapter."""

    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    command_id: UUID
    proposal_id: UUID
    adapter: AdapterName
    input: dict[str, Any]


class AdapterResult(BaseModel):
    """Output from an execution adapter."""

    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    command_id: UUID
    adapter: AdapterName
    success: bool
    output: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None

    @model_validator(mode="after")
    def validate_error(self) -> AdapterResult:
        if self.success and self.error is not None:
            raise ValueError("successful adapter result must not set error")
        if not self.success and (self.error is None or not self.error.strip()):
            raise ValueError("failed adapter result must set error")
        return self


class ExecutionAdapter(Protocol):
    """Protocol implemented by external-system adapters."""

    @property
    def name(self) -> AdapterName: ...

    async def execute(self, request: AdapterRequest) -> AdapterResult: ...


__all__ = [
    "AdapterName",
    "AdapterRequest",
    "AdapterResult",
    "ExecutionAdapter",
]

