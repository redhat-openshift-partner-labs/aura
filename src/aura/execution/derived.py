"""A proposal's derived lifecycle state (Phase 3 Records and State).

A proposal's `status` is inside its digest, so its state is derived from
the records that name it. Stage 2 has no execution records, so the states
here are the first ones in the plan's precedence: rejected, stale,
approved, awaiting approval, and ready.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from enum import StrEnum

from aura.approvals import ApprovalDecision, ApprovalRecord
from aura.execution.action import action_digest
from aura.proposals import Proposal, ProposalType


class DerivedState(StrEnum):
    PROPOSAL_READY = "proposal_ready"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    STALE = "stale"
    REJECTED = "rejected"


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
) -> DerivedState:
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


__all__ = ["DerivedState", "derive_state", "required_approvers"]
