"""A proposal's derived state from its approval records (task 2.5)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from aura.approvals import ApprovalRecord
from aura.execution.action import action_digest
from aura.execution.derived import DerivedState, derive_state, required_approvers
from test_approvals import approval
from test_proposals import make_proposal


NOW = datetime(2026, 10, 30, 12, tzinfo=UTC)


def record(proposal, number: int, **changes) -> ApprovalRecord:
    created = NOW - timedelta(hours=1)
    fields = {
        "approval_id": str(UUID(int=number)),
        "proposal_id": str(proposal.proposal_id),
        "proposal_digest": proposal.proposal_digest,
        "action_digest": action_digest(proposal),
        "created_at": created.isoformat(),
        "expires_at": (created + timedelta(hours=24)).isoformat(),
        **changes,
    }
    return ApprovalRecord.model_validate(approval(**fields))


def rejection(proposal, number: int) -> ApprovalRecord:
    return record(proposal, number, decision="rejected", expires_at=None)


def state(proposal, approvals, *, stale=False, published=True) -> DerivedState:
    return derive_state(
        proposal, approvals, git_stale=stale, published=published, now=NOW
    )


def test_deprovision_needs_two_approvers_and_others_one() -> None:
    assert required_approvers(make_proposal("deprovision")) == 2
    for kind in ("scaffold", "cluster_edit", "post_provision_rerun"):
        assert required_approvers(make_proposal(kind)) == 1


def test_a_published_proposal_without_records_is_ready() -> None:
    assert state(make_proposal("cluster_edit"), []) is DerivedState.PROPOSAL_READY


def test_an_unpublished_proposal_is_ready() -> None:
    assert state(make_proposal("cluster_edit"), [], published=False) is (
        DerivedState.PROPOSAL_READY
    )


def test_one_approval_approves_a_cluster_edit() -> None:
    proposal = make_proposal("cluster_edit")

    assert state(proposal, [record(proposal, 1)]) is DerivedState.APPROVED


def test_one_approval_leaves_a_deprovision_awaiting_approval() -> None:
    proposal = make_proposal("deprovision")

    assert state(proposal, [record(proposal, 1)]) is DerivedState.AWAITING_APPROVAL


def test_two_distinct_approvers_approve_a_deprovision() -> None:
    proposal = make_proposal("deprovision")
    approvals = [
        record(proposal, 1),
        record(proposal, 2, principal="second@example.com"),
    ]

    assert state(proposal, approvals) is DerivedState.APPROVED


def test_one_principal_twice_counts_once() -> None:
    proposal = make_proposal("deprovision")

    assert state(proposal, [record(proposal, 1), record(proposal, 2)]) is (
        DerivedState.AWAITING_APPROVAL
    )


def test_all_approvals_expired_leaves_it_awaiting_approval() -> None:
    proposal = make_proposal("cluster_edit")
    created = NOW - timedelta(hours=25)
    expired = record(
        proposal,
        1,
        created_at=created.isoformat(),
        expires_at=(created + timedelta(hours=24)).isoformat(),
    )

    assert state(proposal, [expired]) is DerivedState.AWAITING_APPROVAL


@pytest.mark.parametrize(
    "changes",
    [{"proposal_digest": "9" * 64}, {"action_digest": "9" * 64}],
    ids=["other-proposal-digest", "other-action-digest"],
)
def test_an_approval_of_another_version_does_not_count(changes) -> None:
    proposal = make_proposal("cluster_edit")

    assert state(proposal, [record(proposal, 1, **changes)]) is (
        DerivedState.AWAITING_APPROVAL
    )


def test_a_rejection_wins_over_approvals() -> None:
    proposal = make_proposal("cluster_edit")

    assert state(proposal, [record(proposal, 1), rejection(proposal, 2)]) is (
        DerivedState.REJECTED
    )


def test_a_rejection_alone_rejects() -> None:
    proposal = make_proposal("cluster_edit")

    assert state(proposal, [rejection(proposal, 1)]) is DerivedState.REJECTED


def test_a_stale_proposal_is_stale_even_when_approved() -> None:
    proposal = make_proposal("cluster_edit")

    assert state(proposal, [record(proposal, 1)], stale=True) is DerivedState.STALE


def test_a_rejection_wins_over_staleness() -> None:
    proposal = make_proposal("cluster_edit")

    assert state(proposal, [rejection(proposal, 1)], stale=True) is (
        DerivedState.REJECTED
    )


def test_approvals_of_another_proposal_do_not_count() -> None:
    proposal = make_proposal("cluster_edit")
    other = record(proposal, 1, proposal_id=str(UUID(int=4242)))

    assert state(proposal, [other]) is DerivedState.PROPOSAL_READY
