"""The action subset that approvals sign and the guard compares (task 3.2)."""

from __future__ import annotations

import copy
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest

from aura.execution.action import ACTION_FIELDS, EXCLUDED_FIELDS, action_digest
from aura.proposals import (
    Proposal,
    ProposalType,
    Reversibility,
    TriggerEffect,
)
from test_proposals import make_proposal


TYPES = ["deprovision", "scaffold", "cluster_edit", "post_provision_rerun"]


def variant(proposal: Proposal, **changes: Any) -> Proposal:
    # model_copy skips validation, so one field changes and nothing else.
    return proposal.model_copy(update=changes)


def test_every_proposal_field_is_classified() -> None:
    assert set(ACTION_FIELDS) | set(EXCLUDED_FIELDS) == set(Proposal.model_fields)
    assert not set(ACTION_FIELDS) & set(EXCLUDED_FIELDS)


@pytest.mark.parametrize("proposal_type", TYPES)
def test_the_digest_is_deterministic(proposal_type) -> None:
    proposal = make_proposal(proposal_type)

    assert action_digest(proposal) == action_digest(make_proposal(proposal_type))
    assert len(action_digest(proposal)) == 64


def manifest_with(proposal: Proposal, edit) -> dict[str, Any]:
    manifest = copy.deepcopy(proposal.pipeline_run)
    edit(manifest)
    return manifest


INCLUDED = [
    ("deprovision", {"proposal_type": ProposalType.CLUSTER_EDIT}),
    ("deprovision", {"cluster_name": "other-cluster"}),
    ("deprovision", {"trigger_effect": [TriggerEffect.DEPROVISION]}),
    ("deprovision", {"commit_message": "chore: deprovision another"}),
    ("deprovision", {"patch": "diff --git a/x b/x\n"}),
    ("scaffold", {"scaffold_commit": "1" * 40}),
]


@pytest.mark.parametrize(
    ("proposal_type", "changes"),
    INCLUDED,
    ids=[next(iter(changes)) for _, changes in INCLUDED],
)
def test_an_included_field_changes_the_digest(proposal_type, changes) -> None:
    proposal = make_proposal(proposal_type)

    assert action_digest(variant(proposal, **changes)) != action_digest(proposal)


def test_a_pipeline_run_spec_change_changes_the_digest() -> None:
    proposal = make_proposal("post_provision_rerun")
    manifest = manifest_with(
        proposal, lambda m: m["spec"]["params"].append({"name": "x", "value": "y"})
    )

    assert action_digest(variant(proposal, pipeline_run=manifest)) != (
        action_digest(proposal)
    )


def test_another_annotation_changes_the_digest() -> None:
    proposal = make_proposal("post_provision_rerun")
    manifest = manifest_with(
        proposal,
        lambda m: m["metadata"].setdefault("annotations", {}).update(
            {"example.com/owner": "operator"}
        ),
    )

    assert action_digest(variant(proposal, pipeline_run=manifest)) != (
        action_digest(proposal)
    )


EXCLUDED = {
    "proposal_id": UUID(int=99),
    "command_id": UUID(int=98),
    "created_at": datetime(2027, 1, 1, tzinfo=UTC),
    "base_commit": "9" * 40,
    "evidence_snapshot_id": UUID(int=97),
    "evidence_snapshot_digest": "9" * 64,
    "cwa_snapshot_id": UUID(int=96),
    "cwa_trace_id": UUID(int=95),
    "generator": "other",
    "generator_version": "9.9.9",
    "summary": "Another summary.",
    "expected_effect": "Another effect.",
    "risks": ["Another risk."],
    "reversibility": Reversibility.IRREVERSIBLE,
    "rollback": "Another rollback.",
    "proposal_digest": "9" * 64,
    "patch_check": None,
}


@pytest.mark.parametrize("field", sorted(EXCLUDED))
@pytest.mark.parametrize("proposal_type", ["deprovision", "scaffold"])
def test_an_excluded_field_does_not_change_the_digest(proposal_type, field) -> None:
    proposal = make_proposal(proposal_type)

    assert action_digest(variant(proposal, **{field: EXCLUDED[field]})) == (
        action_digest(proposal)
    )


def test_scaffold_run_does_not_change_the_digest() -> None:
    proposal = make_proposal("scaffold")
    run = proposal.scaffold_run.model_copy(update={"stdout": "other output"})

    assert action_digest(variant(proposal, scaffold_run=run)) == (
        action_digest(proposal)
    )


def test_trigger_effect_order_does_not_change_the_digest() -> None:
    proposal = make_proposal("deprovision")

    assert action_digest(
        variant(proposal, trigger_effect=list(reversed(proposal.trigger_effect)))
    ) == action_digest(proposal)


def test_the_proposal_trailer_does_not_change_the_digest() -> None:
    proposal = make_proposal("deprovision")
    first = variant(
        proposal, commit_message=f"{proposal.commit_message}\n\nAura-Proposal: {UUID(int=1)}\n"
    )
    second = variant(
        proposal, commit_message=f"{proposal.commit_message}\n\nAura-Proposal: {UUID(int=2)}\n"
    )

    assert action_digest(first) == action_digest(second)


@pytest.mark.parametrize(
    "edit",
    [
        lambda m: m["metadata"].update(name="aura-rerun-x"),
        lambda m: m["metadata"].update(generateName="post-provision-other-"),
        lambda m: m["metadata"].setdefault("annotations", {}).update(
            {"aura.openshiftpartnerlabs.com/proposal-id": "other"}
        ),
    ],
    ids=["name", "generate-name", "aura-annotation"],
)
def test_aura_naming_in_the_pipeline_run_does_not_change_the_digest(edit) -> None:
    proposal = make_proposal("post_provision_rerun")

    assert action_digest(
        variant(proposal, pipeline_run=manifest_with(proposal, edit))
    ) == action_digest(proposal)
