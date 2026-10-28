"""The Git patch-check outcome is recorded on the proposal and its CWA
trace (Phase 3 task 1.8)."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

import aura.cli as cli
from aura.canonical import canonical_digest
from aura.contracts import PatchCheckOutcome, PatchCheckRecord
from aura.cwa import CWATrace
from aura.proposals import Proposal
from failure_injection import run_cli
from test_failure_matrix import COMMAND_ID, propose, scenario_named
from test_patch_check import RecordingCheck
from test_proposals import make_proposal


def record_for(proposal: Proposal, **changes: Any) -> dict[str, Any]:
    return {
        "base_commit": proposal.base_commit,
        "cluster_name": proposal.cluster_name,
        "patch_digest": hashlib.sha256(
            str(proposal.patch).encode("utf-8")
        ).hexdigest(),
        "require_definition": proposal.proposal_type != "deprovision",
        "outcome": (
            "applies" if proposal.proposal_type == "deprovision" else "complete"
        ),
        "reasons": [],
        **changes,
    }


@pytest.mark.parametrize(
    "changes",
    [
        {"outcome": "refused"},
        {"reasons": ["unexpected"]},
        {"outcome": "complete", "require_definition": False},
        {"outcome": "applies", "require_definition": True},
        {"base_commit": "not-a-commit"},
        {"patch_digest": "short"},
    ],
    ids=[
        "refused-without-reasons",
        "reasons-without-refusal",
        "complete-without-definition",
        "applies-with-definition",
        "bad-commit",
        "bad-digest",
    ],
)
def test_patch_check_record_rejects_inconsistent_values(changes) -> None:
    with pytest.raises(ValidationError):
        PatchCheckRecord.model_validate(
            record_for(make_proposal("deprovision"), **changes)
        )


def test_refused_record_carries_its_reasons() -> None:
    record = PatchCheckRecord.model_validate(
        record_for(
            make_proposal("deprovision"),
            outcome="refused",
            reasons=["patch does not apply"],
        )
    )

    assert record.outcome is PatchCheckOutcome.REFUSED


@pytest.mark.parametrize("proposal_type", ["deprovision", "cluster_edit"])
def test_proposal_accepts_its_own_patch_check(proposal_type) -> None:
    proposal = make_proposal(proposal_type)

    checked = make_proposal(
        proposal_type, patch_check=record_for(proposal)
    )

    assert checked.patch_check is not None
    assert checked.proposal_digest != proposal.proposal_digest


@pytest.mark.parametrize(
    "changes",
    [
        {"patch_digest": "1" * 64},
        {"base_commit": "2" * 40},
        {"cluster_name": "other"},
        {"outcome": "refused", "reasons": ["patch does not apply"]},
        {"require_definition": True, "outcome": "complete"},
    ],
    ids=["patch", "base-commit", "cluster", "refused", "definition"],
)
def test_proposal_rejects_a_patch_check_of_another_patch(changes) -> None:
    proposal = make_proposal("deprovision")

    with pytest.raises(ValidationError):
        make_proposal(
            "deprovision", patch_check=record_for(proposal, **changes)
        )


def test_rerun_proposal_rejects_a_patch_check() -> None:
    record = record_for(make_proposal("deprovision"))

    with pytest.raises(ValidationError):
        make_proposal("post_provision_rerun", patch_check=record)


def test_proposal_without_a_patch_check_keeps_its_digest() -> None:
    # Proposals stored before the field existed must still verify.
    proposal = make_proposal("deprovision")
    legacy = proposal.model_dump(
        mode="json", exclude={"proposal_digest", "patch_check"}
    )

    assert proposal.proposal_digest == canonical_digest(legacy)


def stored(root: Path, directory: str) -> dict[str, Any]:
    (path,) = (root / directory).glob("*.json")
    return json.loads(path.read_text(encoding="utf-8"))


def run_scenario(root: Path, monkeypatch, build) -> Any:
    arguments, collector = build(root)
    return run_cli(
        arguments,
        monkeypatch,
        collector=collector,
        command_id=COMMAND_ID,
        clock=lambda: datetime(2026, 1, 1, tzinfo=UTC),
    )


@pytest.mark.parametrize(
    ("name", "outcome"),
    [
        ("propose", "applies"),
        ("cluster-edit-file", "complete"),
        ("scaffold", "complete"),
    ],
)
def test_stored_proposal_and_trace_record_the_patch_check(
    tmp_path,
    monkeypatch,
    name,
    outcome,
) -> None:
    run = run_scenario(tmp_path, monkeypatch, scenario_named(name).build)

    assert run.exit_code == 0, run.stderr
    proposal = Proposal.model_validate(stored(tmp_path, "proposal"))
    trace = CWATrace.model_validate(stored(tmp_path, "cwa/traces"))
    assert proposal.patch_check is not None
    assert proposal.patch_check.outcome == outcome
    assert proposal.patch_check.base_commit == proposal.base_commit
    assert trace.patch_check == proposal.patch_check


def test_rerun_proposal_and_trace_have_no_patch_check(
    tmp_path,
    monkeypatch,
) -> None:
    run = run_scenario(tmp_path, monkeypatch, scenario_named("rerun").build)

    assert run.exit_code == 0, run.stderr
    assert stored(tmp_path, "proposal")["patch_check"] is None
    assert stored(tmp_path, "cwa/traces")["patch_check"] is None


def test_refused_patch_check_is_recorded_on_the_trace(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        cli,
        "GitPatchCheck",
        lambda fleet_clusters: RecordingCheck("patch does not apply"),
    )

    run = run_scenario(tmp_path, monkeypatch, propose(None))

    assert run.exit_code == 2
    assert not (tmp_path / "proposal").exists() or not list(
        (tmp_path / "proposal").glob("*.json")
    )
    trace = CWATrace.model_validate(stored(tmp_path, "cwa/traces"))
    assert trace.patch_check is not None
    assert trace.patch_check.outcome is PatchCheckOutcome.REFUSED
    assert trace.patch_check.reasons == ["patch does not apply"]
    assert trace.patch_check.cluster_name == "example"
    assert trace.response_digest is None
