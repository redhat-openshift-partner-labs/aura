from __future__ import annotations

import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from aura.authorization import OperatorRole
from aura.collectors.cluster_state import ClusterStateCollector
from aura.collectors.git_inventory import GitInventoryCollector
from aura.contracts import (
    CommandEnvelope,
    CommandState,
    CommandType,
    EvidenceSnapshot,
)
from aura.proposals import (
    SKIP_DEPROVISION,
    Proposal,
    ProposalType,
    Reversibility,
    TriggerEffect,
    patch_changes,
)
from aura.services.proposal import (
    DeprovisionProposalGenerator,
    InvalidProposalCommand,
    ProposalRefused,
    ProposalService,
    UnauthorizedProposalCommand,
)
from test_cluster_state_collector import (
    EmptyCollector,
    FakeGitCollector,
    FakeOpenShiftCollector,
    FakeTektonCollector,
)
from test_cluster_state_diagnosis import assert_references, make_cwa_snapshot
from test_git_inventory_collector import commit_repository, write_cluster


TRACE_ID = UUID(int=70)
CREATED_AT = datetime(2026, 1, 1, tzinfo=UTC)


def cluster_state(
    root: Path,
    *,
    git_collector: object | None = None,
    openshift_collector: object | None = None,
) -> EvidenceSnapshot:
    return ClusterStateCollector(
        git_collector=git_collector or GitInventoryCollector(root),
        openshift_collector=openshift_collector or FakeOpenShiftCollector(),
        tekton_collector=FakeTektonCollector(),
        cluster_name="example",
    ).collect(
        command_id=UUID(int=1),
        snapshot_id=UUID(int=40),
        collected_at=CREATED_AT,
    )


def generate(evidence: EvidenceSnapshot) -> Proposal:
    return DeprovisionProposalGenerator().generate(
        make_cwa_snapshot(evidence),
        cwa_trace_id=TRACE_ID,
        created_at=CREATED_AT,
    )


def test_generator_proposes_standalone_archive_sentinel(
    tmp_path: Path,
) -> None:
    write_cluster(tmp_path)
    base = commit_repository(tmp_path)
    evidence = cluster_state(tmp_path)

    proposal = generate(evidence)

    assert proposal.proposal_type is ProposalType.DEPROVISION
    assert proposal.cluster_name == "example"
    assert proposal.base_commit == base
    assert proposal.command_id == UUID(int=1)
    assert_references(proposal, evidence, TRACE_ID)
    assert proposal.generator == "deprovision-sentinel-deterministic"
    assert patch_changes(proposal.patch or "") == [
        ("deprovision/example-archive", "added")
    ]
    assert proposal.trigger_effect == [
        TriggerEffect.DEPROVISION,
        TriggerEffect.ARCHIVE_ACTION,
    ]
    assert proposal.reversibility is Reversibility.REVERSIBLE_BEFORE_MERGE
    message = proposal.commit_message or ""
    assert message.startswith("Request deprovision of cluster example")
    assert "standalone commit" in message
    assert str(proposal.proposal_id) in message
    assert SKIP_DEPROVISION not in message
    assert Proposal.model_validate_json(proposal.model_dump_json()) == proposal
    assert generate(evidence) == proposal
    subprocess.run(
        ["git", "-C", str(tmp_path), "apply", "--check", "-"],
        input=proposal.patch,
        check=True,
        capture_output=True,
        text=True,
    )


def test_generator_accepts_legacy_incomplete_definition(
    tmp_path: Path,
) -> None:
    write_cluster(tmp_path)
    (tmp_path / "provision" / "example" / "metadata.yaml").unlink()
    commit_repository(tmp_path)

    proposal = generate(cluster_state(tmp_path))

    assert patch_changes(proposal.patch or "") == [
        ("deprovision/example-archive", "added")
    ]


def _with_sentinel(name: str) -> Callable[[Path], dict[str, object]]:
    def setup(root: Path) -> dict[str, object]:
        write_cluster(root)
        (root / "deprovision").mkdir()
        (root / "deprovision" / name).write_text("", encoding="utf-8")
        return {}

    return setup


def _archived(root: Path) -> dict[str, object]:
    write_cluster(root, directory="archive")
    return {}


def _conflicting(root: Path) -> dict[str, object]:
    write_cluster(root)
    write_cluster(root, directory="archive")
    return {}


def _undefined(root: Path) -> dict[str, object]:
    write_cluster(root, cluster_name="other")
    return {}


def _without_openshift(root: Path) -> dict[str, object]:
    write_cluster(root)
    return {"openshift_collector": EmptyCollector()}


def _unpinned_git(root: Path) -> dict[str, object]:
    write_cluster(root)
    return {"git_collector": FakeGitCollector()}


@pytest.mark.parametrize(
    ("setup", "reason"),
    [
        (
            _with_sentinel("example"),
            "deprovision sentinel already exists: deprovision/example",
        ),
        (
            _with_sentinel("example-archive"),
            "deprovision sentinel already exists: "
            "deprovision/example-archive",
        ),
        (
            _archived,
            "cluster example is defined at archive/example, "
            "not provision/example",
        ),
        (_conflicting, "conflicting Git cluster definitions for example"),
        (_undefined, "no Git cluster definition for example"),
        (_without_openshift, "no OpenShift resources for cluster example"),
        (_unpinned_git, "no pinned Git base commit in evidence"),
    ],
)
def test_generator_refuses_unmet_preconditions(
    tmp_path: Path,
    setup: Callable[[Path], dict[str, object]],
    reason: str,
) -> None:
    collectors = setup(tmp_path)
    commit_repository(tmp_path)

    with pytest.raises(ProposalRefused) as refusal:
        generate(cluster_state(tmp_path, **collectors))

    assert reason in refusal.value.reasons
    assert reason in str(refusal.value)


def test_generator_refuses_without_correlation() -> None:
    evidence = EmptyCollector().collect(command_id=UUID(int=1))

    with pytest.raises(ProposalRefused) as refusal:
        generate(evidence)

    assert refusal.value.reasons == [
        "no cluster state correlation in evidence"
    ]


def propose_command(
    command_type: CommandType = CommandType.PROPOSE_PATCH,
) -> CommandEnvelope:
    return CommandEnvelope(
        command_id=UUID(int=1),
        command_type=command_type,
        operator_id="operator@example.com",
        created_at=CREATED_AT,
        input={"query": "propose deprovision"},
        idempotency_key="test-key",
        status=CommandState.RECEIVED,
    )


@pytest.mark.parametrize("role", [OperatorRole.PROPOSER, OperatorRole.ADMIN])
def test_proposal_service_collects_for_proposers(role: OperatorRole) -> None:
    result = ProposalService(collector=EmptyCollector()).run(
        propose_command(),
        {role},
    )

    assert result.command.status is CommandState.ASSEMBLING_CONTEXT


@pytest.mark.parametrize(
    "role",
    [OperatorRole.VIEWER, OperatorRole.APPROVER, OperatorRole.EXECUTOR],
)
def test_proposal_service_rejects_other_roles(role: OperatorRole) -> None:
    with pytest.raises(UnauthorizedProposalCommand):
        ProposalService(collector=EmptyCollector()).run(
            propose_command(),
            {role},
        )


def test_proposal_service_rejects_other_command_types() -> None:
    with pytest.raises(InvalidProposalCommand):
        ProposalService(collector=EmptyCollector()).run(
            propose_command(CommandType.DIAGNOSE_STATE),
            {OperatorRole.PROPOSER},
        )
