"""Signed records of Phase 3 executions (task 3.1).

All records of one execution share its `execution_id`. The store allows
one `dispatching` record per proposal and one terminal record per
execution, so a proposal is dispatched at most once.
"""

from __future__ import annotations

from enum import StrEnum
from typing import ClassVar
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from aura.adapters import AdapterName
from aura.canonical import canonical_json
from aura.contracts import assign_self_digest
from aura.proposals import CLUSTER_NAME_PATTERN, GIT_PROPOSAL_TYPES, ProposalType


_DIGEST = r"^[0-9a-f]{64}$"
_SSH_SIGNATURE = (
    r"^-----BEGIN SSH SIGNATURE-----\n[A-Za-z0-9+/=\n]+\n"
    r"-----END SSH SIGNATURE-----\n?$"
)
PIPELINE_NAMESPACE = "openshift-pipelines"


class ExecutionPhase(StrEnum):
    DISPATCHING = "dispatching"
    SUBMITTED = "submitted"
    HANDED_OFF = "handed_off"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    MERGED = "merged"
    CLOSED = "closed"
    NOT_DISPATCHED = "not_dispatched"


TERMINAL_PHASES = frozenset(
    {
        ExecutionPhase.HANDED_OFF,
        ExecutionPhase.SUCCEEDED,
        ExecutionPhase.FAILED,
        ExecutionPhase.MERGED,
        ExecutionPhase.CLOSED,
        ExecutionPhase.NOT_DISPATCHED,
    }
)
# Phases that name an external effect.
_WITH_RESULT = frozenset({ExecutionPhase.SUBMITTED, ExecutionPhase.HANDED_OFF})
_EVERY_ADAPTER = frozenset(
    {ExecutionPhase.DISPATCHING, ExecutionPhase.FAILED, ExecutionPhase.NOT_DISPATCHED}
)
# The phases each adapter's executions pass through.
_ADAPTER_PHASES = {
    AdapterName.GIT_HANDOFF: _EVERY_ADAPTER | {ExecutionPhase.HANDED_OFF},
    AdapterName.GIT_PR: _EVERY_ADAPTER
    | {ExecutionPhase.SUBMITTED, ExecutionPhase.MERGED, ExecutionPhase.CLOSED},
    AdapterName.TEKTON: _EVERY_ADAPTER
    | {ExecutionPhase.SUBMITTED, ExecutionPhase.RUNNING, ExecutionPhase.SUCCEEDED},
    AdapterName.RECORDING: _EVERY_ADAPTER | {ExecutionPhase.SUBMITTED},
}


class Resolution(StrEnum):
    TIMEOUT_MARGIN = "timeout_margin"
    MANUAL = "manual"


_ADAPTERS = {
    True: {AdapterName.GIT_HANDOFF, AdapterName.GIT_PR, AdapterName.RECORDING},
    False: {AdapterName.TEKTON, AdapterName.RECORDING},
}


def _not_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("must not be blank")
    return value


class _Signed(BaseModel):
    """A record signed with namespace aura-execute over every field but its
    signature and own digest."""

    _digest_field: ClassVar[str] = ""
    # Optional fields added after records were stored: left out of the
    # digest and signed payload while absent.
    _omit_if_none: ClassVar[frozenset[str]] = frozenset()

    def signed_payload(self) -> bytes:
        absent = {name for name in self._omit_if_none if getattr(self, name) is None}
        return canonical_json(
            self.model_dump(mode="json", exclude={"signature", self._digest_field, *absent})
        ).encode("utf-8")


class ExecutionRecord(_Signed):
    model_config = ConfigDict(extra="forbid")
    _digest_field: ClassVar[str] = "execution_digest"
    _omit_if_none: ClassVar[frozenset[str]] = frozenset({"adapter_timeout_seconds"})

    record_id: UUID
    execution_id: UUID
    command_id: UUID
    proposal_id: UUID
    proposal_digest: str = Field(pattern=_DIGEST)
    action_digest: str = Field(pattern=_DIGEST)
    proposal_type: ProposalType
    cluster_name: str = Field(pattern=CLUSTER_NAME_PATTERN)
    approval_ids: list[UUID] = Field(min_length=1)
    adapter: AdapterName
    adapter_version: str = Field(min_length=1)
    phase: ExecutionPhase
    external_result: dict[str, str] | None = None
    failure_reason: str | None = None
    rollback: str = Field(min_length=1)
    resolution: Resolution | None = None
    confirmation_id: UUID | None = None
    # On a dispatching record: the timeout the adapter call ran under, which
    # `execution status` waits out before recording not_dispatched.
    adapter_timeout_seconds: int | None = Field(default=None, gt=0)
    audit_event_digest: str = Field(pattern=_DIGEST)
    principal: str = Field(min_length=1)
    signature: str = Field(pattern=_SSH_SIGNATURE)
    created_at: AwareDatetime
    execution_digest: str = Field(default="")

    _text = field_validator("adapter_version", "rollback", "principal")(_not_blank)

    @model_validator(mode="after")
    def validate_phase(self) -> "ExecutionRecord":
        if len(set(self.approval_ids)) != len(self.approval_ids):
            raise ValueError("approval_ids must not repeat")
        git = self.proposal_type in GIT_PROPOSAL_TYPES
        if self.adapter not in _ADAPTERS[git]:
            raise ValueError(
                f"adapter {self.adapter.value} cannot execute "
                f"{self.proposal_type.value} proposals"
            )
        if self.phase not in _ADAPTER_PHASES[self.adapter]:
            raise ValueError(
                f"adapter {self.adapter.value} has no {self.phase.value} phase"
            )
        _require_phase_fields(self)
        if self.adapter_timeout_seconds is not None and self.phase is not ExecutionPhase.DISPATCHING:
            raise ValueError("only a dispatching record carries adapter_timeout_seconds")
        assign_self_digest(self, "execution_digest", "execution record", self._omit_if_none)
        return self


def _require_phase_fields(record: ExecutionRecord) -> None:
    phase = record.phase
    # A dispatch has no effect yet; a submission or hand-off names its own.
    if phase is ExecutionPhase.DISPATCHING and record.external_result is not None:
        raise ValueError("a dispatching record has no external_result")
    if phase in _WITH_RESULT and not record.external_result:
        raise ValueError(f"a {phase.value} record names its external_result")
    manual = record.resolution is Resolution.MANUAL
    reason_allowed = phase is ExecutionPhase.FAILED or (
        phase is ExecutionPhase.NOT_DISPATCHED and manual
    )
    if reason_allowed != (record.failure_reason is not None):
        raise ValueError(f"failure_reason does not fit phase {phase.value}")
    if record.failure_reason is not None and not record.failure_reason.strip():
        raise ValueError("failure_reason must not be blank")
    if (phase is ExecutionPhase.NOT_DISPATCHED) != (record.resolution is not None):
        raise ValueError("only not_dispatched records carry a resolution")
    if manual != (record.confirmation_id is not None):
        raise ValueError("a manual resolution names its confirmation")


class PipelineRunObservation(_Signed):
    """One read of an Aura-created PipelineRun by `execution status`."""

    model_config = ConfigDict(extra="forbid")
    _digest_field: ClassVar[str] = "observation_digest"

    observation_id: UUID
    execution_id: UUID
    command_id: UUID
    name: str = Field(min_length=1)
    uid: str = Field(min_length=1)
    namespace: str
    succeeded_status: str
    succeeded_reason: str | None = None
    start_time: AwareDatetime | None = None
    completion_time: AwareDatetime | None = None
    observed_at: AwareDatetime
    audit_event_digest: str = Field(pattern=_DIGEST)
    principal: str = Field(min_length=1)
    signature: str = Field(pattern=_SSH_SIGNATURE)
    observation_digest: str = Field(default="")

    _text = field_validator("name", "uid", "principal")(_not_blank)

    @model_validator(mode="after")
    def validate_observation(self) -> "PipelineRunObservation":
        if self.namespace != PIPELINE_NAMESPACE:
            raise ValueError(f"namespace must be {PIPELINE_NAMESPACE}")
        if self.succeeded_status not in {"True", "False", "Unknown"}:
            raise ValueError("succeeded_status must be True, False, or Unknown")
        complete = self.succeeded_status != "Unknown"
        if complete != (self.completion_time is not None):
            raise ValueError("completion_time is set exactly when the run is complete")
        if (
            self.start_time is not None
            and self.completion_time is not None
            and self.completion_time < self.start_time
        ):
            raise ValueError("completion_time precedes start_time")
        assign_self_digest(self, "observation_digest", "PipelineRun observation")
        return self


class ResolutionConfirmation(_Signed):
    """An approver's signed statement that an `awaiting_operator`
    execution made no external effect, which a `manual` resolution names.
    Signed with namespace aura-approve."""

    model_config = ConfigDict(extra="forbid")
    _digest_field: ClassVar[str] = "confirmation_digest"

    confirmation_id: UUID
    execution_id: UUID
    command_id: UUID
    statement: str = Field(min_length=1, max_length=1000)
    audit_event_digest: str = Field(pattern=_DIGEST)
    principal: str = Field(min_length=1)
    signature: str = Field(pattern=_SSH_SIGNATURE)
    created_at: AwareDatetime
    confirmation_digest: str = Field(default="")

    _text = field_validator("statement", "principal")(_not_blank)

    @model_validator(mode="after")
    def validate_digest(self) -> "ResolutionConfirmation":
        assign_self_digest(self, "confirmation_digest", "resolution confirmation")
        return self


__all__ = [
    "ExecutionPhase",
    "ExecutionRecord",
    "PIPELINE_NAMESPACE",
    "PipelineRunObservation",
    "Resolution",
    "ResolutionConfirmation",
    "TERMINAL_PHASES",
]
