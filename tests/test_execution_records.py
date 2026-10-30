"""ExecutionRecord and PipelineRunObservation contracts (task 3.1)."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from aura.canonical import canonical_json
from aura.execution.records import (
    ExecutionPhase,
    ExecutionRecord,
    PipelineRunObservation,
    TERMINAL_PHASES,
)
from test_approvals import SIGNATURE


def execution(**changes: Any) -> dict[str, Any]:
    return {
        "record_id": "00000000-0000-0000-0000-000000000051",
        "execution_id": "00000000-0000-0000-0000-000000000050",
        "command_id": "00000000-0000-0000-0000-000000000011",
        "proposal_id": "00000000-0000-0000-0000-000000000012",
        "proposal_digest": "2" * 64,
        "action_digest": "3" * 64,
        "proposal_type": "post_provision_rerun",
        "cluster_name": "example",
        "approval_ids": ["00000000-0000-0000-0000-000000000010"],
        "adapter": "tekton",
        "adapter_version": "0.1.0",
        "phase": "dispatching",
        "rollback": "No rollback: a PipelineRun cannot be undone.",
        "audit_event_digest": "4" * 64,
        "principal": "executor@example.com",
        "signature": SIGNATURE,
        "created_at": "2026-10-30T12:00:00Z",
        **changes,
    }


def test_a_dispatching_record_validates_and_digests() -> None:
    record = ExecutionRecord.model_validate(execution())

    assert record.phase is ExecutionPhase.DISPATCHING
    assert len(record.execution_digest) == 64


@pytest.mark.parametrize(
    "changes",
    [
        {"phase": "submitted", "external_result": {"pipeline_run": "aura-rerun-x"}},
        {"phase": "running"},
        {"phase": "succeeded"},
        {"phase": "failed", "failure_reason": "create refused"},
        {"phase": "handed_off", "adapter": "git_handoff", "proposal_type": "deprovision",
         "external_result": {"mailbox": "/tmp/aura.mbox"}},
        {"phase": "merged", "adapter": "git_pr", "proposal_type": "cluster_edit"},
        {"phase": "not_dispatched", "resolution": "timeout_margin"},
        {"phase": "not_dispatched", "resolution": "manual",
         "confirmation_id": "00000000-0000-0000-0000-000000000060",
         "failure_reason": "both operators confirmed no PipelineRun exists"},
        {"adapter": "recording"},
        {"adapter": "recording", "proposal_type": "scaffold"},
    ],
    ids=[
        "submitted", "running", "succeeded", "failed", "handed-off", "merged",
        "timeout-resolution", "manual-resolution", "recording-rerun", "recording-git",
    ],
)
def test_each_phase_validates(changes) -> None:
    assert ExecutionRecord.model_validate(execution(**changes))


@pytest.mark.parametrize(
    "changes",
    [
        {"external_result": {"pipeline_run": "x"}},
        {"failure_reason": "early"},
        {"resolution": "timeout_margin"},
        {"phase": "submitted"},
        {"phase": "handed_off", "adapter": "git_handoff", "proposal_type": "deprovision"},
        {"phase": "failed"},
        {"phase": "failed", "failure_reason": " "},
        {"phase": "succeeded", "failure_reason": "not a failure"},
        {"phase": "not_dispatched"},
        {"phase": "not_dispatched", "resolution": "manual",
         "failure_reason": "confirmed"},
        {"phase": "not_dispatched", "resolution": "manual",
         "confirmation_id": "00000000-0000-0000-0000-000000000060"},
        {"phase": "submitted", "external_result": {"x": "y"},
         "confirmation_id": "00000000-0000-0000-0000-000000000060"},
        {"approval_ids": []},
        {"approval_ids": ["00000000-0000-0000-0000-000000000010"] * 2},
        {"adapter": "git_handoff"},
        {"adapter": "tekton", "proposal_type": "deprovision"},
        {"phase": "unknown"},
        {"principal": " "},
        {"signature": "unsigned"},
        {"cluster_name": "Not_DNS"},
        {"rollback": " "},
        {"execution_digest": "5" * 64},
        {"extra": 1},
    ],
    ids=[
        "dispatching-with-result", "dispatching-with-failure", "dispatching-with-resolution",
        "submitted-without-result", "handed-off-without-result", "failed-without-reason",
        "failed-blank-reason", "success-with-reason", "not-dispatched-without-resolution",
        "manual-without-confirmation", "manual-without-reason", "confirmation-off-resolution",
        "no-approvals", "repeated-approval", "git-adapter-for-rerun", "tekton-for-git",
        "unknown-phase", "blank-principal", "bad-signature", "bad-cluster", "blank-rollback",
        "stale-digest", "extra-field",
    ],
)
def test_an_execution_record_rejects_an_invalid_value(changes) -> None:
    with pytest.raises(ValidationError):
        ExecutionRecord.model_validate(execution(**changes))


def test_terminal_phases() -> None:
    assert {phase.value for phase in TERMINAL_PHASES} == {
        "handed_off", "succeeded", "failed", "merged", "closed", "not_dispatched",
    }


def test_the_signed_payload_omits_only_the_signature_and_digest() -> None:
    record = ExecutionRecord.model_validate(execution())
    expected = record.model_dump(mode="json", exclude={"signature", "execution_digest"})

    assert record.signed_payload() == canonical_json(expected).encode()


def observation(**changes: Any) -> dict[str, Any]:
    return {
        "observation_id": "00000000-0000-0000-0000-000000000070",
        "execution_id": "00000000-0000-0000-0000-000000000050",
        "command_id": "00000000-0000-0000-0000-000000000013",
        "name": "aura-rerun-00000000-0000-0000-0000-000000000012",
        "uid": "6f1e2d3c-4b5a-4987-8a6b-5c4d3e2f1a0b",
        "namespace": "openshift-pipelines",
        "succeeded_status": "Unknown",
        "succeeded_reason": "Running",
        "start_time": "2026-10-30T12:01:00Z",
        "completion_time": None,
        "observed_at": "2026-10-30T12:05:00Z",
        "audit_event_digest": "4" * 64,
        "principal": "executor@example.com",
        "signature": SIGNATURE,
        **changes,
    }


def test_an_observation_validates_and_digests() -> None:
    record = PipelineRunObservation.model_validate(observation())

    assert len(record.observation_digest) == 64
    assert record.signed_payload() == canonical_json(
        record.model_dump(mode="json", exclude={"signature", "observation_digest"})
    ).encode()


@pytest.mark.parametrize(
    "changes",
    [
        {"succeeded_status": "Maybe", "completion_time": "2026-10-30T12:10:00Z"},
        {"succeeded_status": "True", "completion_time": None},
        {"succeeded_status": "Unknown", "completion_time": "2026-10-30T12:10:00Z"},
        {"completion_time": "2026-10-30T12:00:00Z", "succeeded_status": "False"},
        {"name": " "},
        {"namespace": "default"},
        {"signature": "unsigned"},
        {"observation_digest": "5" * 64},
    ],
    ids=[
        "unknown-status", "complete-without-time", "running-with-completion",
        "completion-before-start", "blank-name", "other-namespace", "bad-signature",
        "stale-digest",
    ],
)
def test_an_observation_rejects_an_invalid_value(changes) -> None:
    with pytest.raises(ValidationError):
        PipelineRunObservation.model_validate(observation(**changes))
