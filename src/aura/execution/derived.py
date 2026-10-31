"""A proposal's derived lifecycle state (Phase 3 Records and State).

A proposal's `status` is inside its digest, so its state is derived from
the records that name it, first match wins: the highest-ranked execution
result, `awaiting_operator`, `rejected`, `stale`, `approved`,
`awaiting_approval`, and `proposal_ready`.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from enum import StrEnum

from aura.approvals import ApprovalDecision, ApprovalRecord
from aura.execution.action import action_digest
from aura.execution.records import ExecutionPhase, ExecutionRecord, TERMINAL_PHASES
from aura.execution.store import RecordInvalid
from aura.proposals import Proposal, ProposalType


class DerivedState(StrEnum):
    PROPOSAL_READY = "proposal_ready"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    STALE = "stale"
    REJECTED = "rejected"
    AWAITING_OPERATOR = "awaiting_operator"
    SUBMITTED = "submitted"
    HANDED_OFF = "handed_off"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    MERGED = "merged"
    CLOSED = "closed"
    NOT_DISPATCHED = "not_dispatched"


# An execution in one of these states keeps its cluster open (G11).
OPEN_STATES = frozenset(
    {DerivedState.AWAITING_OPERATOR, DerivedState.SUBMITTED, DerivedState.RUNNING}
)


def required_approvers(proposal: Proposal) -> int:
    """D11: deprovision needs two distinct approvers; others need one."""

    return 2 if proposal.proposal_type is ProposalType.DEPROVISION else 1


def derive_state(
    proposal: Proposal,
    approvals: Iterable[ApprovalRecord],
    *,
    git_stale: bool,
    published: bool,
    now: datetime,
    executions: Iterable[ExecutionRecord] = (),
) -> DerivedState:
    executed = execution_state(
        record for record in executions if record.proposal_id == proposal.proposal_id
    )
    if executed is not None:
        return executed
    records = [
        record for record in approvals if record.proposal_id == proposal.proposal_id
    ]
    if any(record.decision is ApprovalDecision.REJECTED for record in records):
        return DerivedState.REJECTED
    if git_stale:
        return DerivedState.STALE
    if not published or not records:
        return DerivedState.PROPOSAL_READY
    action = action_digest(proposal)
    # Only unexpired approvals of this exact proposal and action count,
    # once per principal.
    approvers = {
        record.principal
        for record in records
        if record.proposal_digest == proposal.proposal_digest
        and record.action_digest == action
        and record.expires_at is not None
        and record.expires_at > now
    }
    if len(approvers) >= required_approvers(proposal):
        return DerivedState.APPROVED
    return DerivedState.AWAITING_APPROVAL


def execution_state(records: Iterable[ExecutionRecord]) -> DerivedState | None:
    """The terminal result, else running, else submitted; a dispatch with
    no result is awaiting_operator, and no record is None."""

    phases = {record.phase for record in records}
    if not phases:
        return None
    terminal = phases & TERMINAL_PHASES
    if len(terminal) > 1:
        # The store keeps one terminal record per execution, so only rows
        # of two executions of one proposal can disagree.
        raise RecordInvalid(
            "execution records name conflicting results: "
            + ", ".join(sorted(phase.value for phase in terminal))
        )
    if terminal:
        (phase,) = terminal
        return DerivedState(phase.value)
    for phase in (ExecutionPhase.RUNNING, ExecutionPhase.SUBMITTED):
        if phase in phases:
            return DerivedState(phase.value)
    return DerivedState.AWAITING_OPERATOR


__all__ = [
    "OPEN_STATES",
    "DerivedState",
    "derive_state",
    "execution_state",
    "required_approvers",
]
