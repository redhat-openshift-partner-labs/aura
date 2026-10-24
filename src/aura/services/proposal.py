from __future__ import annotations

import re
from datetime import datetime
from typing import final
from uuid import UUID, uuid5

from aura.contracts import ARCHIVE_SUFFIX, CommandType, EvidenceItem
from aura.cwa import CWASlotName, CWASnapshot
from aura.proposals import (
    EXPECTED_REVERSIBILITY,
    EXPECTED_TRIGGER_EFFECTS,
    Proposal,
    ProposalType,
    deprovision_sentinel_patch,
)
from aura.services.diagnosis import cwa_evidence_items
from aura.services.evidence import (
    EvidenceCommandService,
    make_evidence_command_spec,
)


_PINNED_GIT_REVISION = re.compile(r"(?:^|;)git=([0-9a-f]{40})(?:;|$)")


class UnauthorizedProposalCommand(Exception):
    """Raised when an operator cannot issue a proposal command."""


class InvalidProposalCommand(Exception):
    """Raised when a command is not a patch proposal command."""


class ProposalRefused(Exception):
    """Raised when evidence does not satisfy a proposal's preconditions."""

    def __init__(self, reasons: list[str]) -> None:
        super().__init__("; ".join(reasons))
        self.reasons = reasons


class ProposalService(EvidenceCommandService):
    """Collects read-only evidence for an authorized patch proposal."""

    spec = make_evidence_command_spec(
        command_type=CommandType.PROPOSE_PATCH,
        unauthorized_error=UnauthorizedProposalCommand,
        invalid_error=InvalidProposalCommand,
        action="propose changes",
    )


class PipelineProposalService(EvidenceCommandService):
    """Collects read-only evidence for an authorized PipelineRun proposal."""

    spec = make_evidence_command_spec(
        command_type=CommandType.PROPOSE_PIPELINE,
        unauthorized_error=UnauthorizedProposalCommand,
        invalid_error=InvalidProposalCommand,
        action="propose changes",
    )


class UnauthorizedProposalStatusCommand(Exception):
    """Raised when an operator cannot check proposal staleness."""


class InvalidProposalStatusCommand(Exception):
    """Raised when a command is not a proposal status command."""


class ProposalStatusService(EvidenceCommandService):
    """Reports read-only staleness of stored proposals."""

    spec = make_evidence_command_spec(
        command_type=CommandType.PROPOSAL_STATUS,
        unauthorized_error=UnauthorizedProposalStatusCommand,
        invalid_error=InvalidProposalStatusCommand,
        action="check proposal staleness",
    )


@final
class DeprovisionProposalGenerator:
    """Deterministically proposes a standalone deprovision sentinel commit."""

    provider_name = "aura"
    generator = "deprovision-sentinel-deterministic"
    generator_version = "0.1.0"

    def generate(
        self,
        snapshot: CWASnapshot,
        *,
        cwa_trace_id: UUID,
        created_at: datetime,
    ) -> Proposal:
        items = cwa_evidence_items(snapshot)
        correlation = correlation_item(items)
        cluster = str(correlation.data["cluster_name"])
        items_by_id = {str(item.item_id): item for item in items}
        base_commit = pinned_git_revision(snapshot)
        reasons = cluster_preconditions(correlation, items_by_id, cluster)
        if base_commit is None:
            reasons.append("no pinned Git base commit in evidence")
        if reasons:
            raise ProposalRefused(reasons)

        proposal_id = uuid5(
            snapshot.command_id,
            f"proposal:{snapshot.snapshot_id}",
        )
        proposal_type = ProposalType.DEPROVISION
        return Proposal(
            proposal_id=proposal_id,
            command_id=snapshot.command_id,
            proposal_type=proposal_type,
            cluster_name=cluster,
            created_at=created_at,
            base_commit=base_commit,
            evidence_snapshot_id=snapshot.evidence_snapshot_id,
            evidence_snapshot_digest=snapshot.evidence_snapshot_digest,
            cwa_snapshot_id=snapshot.snapshot_id,
            cwa_trace_id=cwa_trace_id,
            generator=self.generator,
            generator_version=self.generator_version,
            trigger_effect=EXPECTED_TRIGGER_EFFECTS[proposal_type],
            reversibility=EXPECTED_REVERSIBILITY[proposal_type],
            **_deprovision_text(cluster, proposal_id),
        )


def _deprovision_text(cluster: str, proposal_id: UUID) -> dict[str, object]:
    sentinel = f"deprovision/{cluster}{ARCHIVE_SUFFIX}"
    return {
        "summary": (
            f"Request deprovision of cluster {cluster} by adding {sentinel}."
        ),
        "expected_effect": (
            f"The deprovision pipeline removes cluster {cluster}; on success "
            f"the archive-cluster Action moves provision/{cluster} to "
            f"archive/{cluster} and removes {sentinel}."
        ),
        "commit_message": (
            f"Request deprovision of cluster {cluster}\n\n"
            f"Add only {sentinel} as a standalone commit. Merging starts the "
            "deprovision pipeline; on success the archive-cluster Action "
            f"moves provision/{cluster} to archive/{cluster}.\n\n"
            f"Aura-Proposal: {proposal_id}\n"
        ),
        "risks": [
            "Merging starts the deprovision pipeline, which destroys "
            f"cluster {cluster} and its cloud resources.",
            "Only the first cluster name in a push starts a pipeline, so "
            "this commit must be pushed alone.",
            "If the deprovision pipeline fails, the archive-cluster Action "
            f"does not run and provision/{cluster} remains.",
        ],
        "rollback": (
            "Discard the proposal before merge. Once the deprovision "
            "pipeline starts, no rollback exists."
        ),
        "patch": deprovision_sentinel_patch(cluster),
    }

def correlation_item(items: list[EvidenceItem]) -> EvidenceItem:
    """Return the cluster-state correlation item, or refuse without one."""

    correlation = next(
        (item for item in items if item.kind == "cluster_state_correlation"),
        None,
    )
    if correlation is None:
        raise ProposalRefused(["no cluster state correlation in evidence"])
    return correlation


def pinned_git_revision(snapshot: CWASnapshot) -> str | None:
    evidence_slot = next(
        slot
        for slot in snapshot.slots
        if slot.name is CWASlotName.EVIDENCE_TOOL_RESULTS
    )
    match = _PINNED_GIT_REVISION.search(
        str(evidence_slot.content.get("source_version", ""))
    )
    return match.group(1) if match else None


def cluster_preconditions(
    correlation: EvidenceItem,
    items_by_id: dict[str, EvidenceItem],
    cluster: str,
) -> list[str]:
    """Return unmet preconditions shared by proposals for a live cluster."""

    reasons: list[str] = []
    git_ids = correlation.data.get("git_evidence_ids", [])
    if len(git_ids) > 1:
        reasons.append(f"conflicting Git cluster definitions for {cluster}")
    elif not git_ids:
        reasons.append(f"no Git cluster definition for {cluster}")
    else:
        path = items_by_id[git_ids[0]].data.get("path")
        if path != f"provision/{cluster}":
            reasons.append(
                f"cluster {cluster} is defined at {path}, "
                f"not provision/{cluster}"
            )
    reasons.extend(
        "deprovision sentinel already exists: "
        f"{items_by_id[item_id].data['path']}"
        for item_id in correlation.data.get(
            "deprovision_sentinel_evidence_ids", []
        )
    )
    if correlation.data.get("openshift_evidence_id") is None:
        reasons.append(f"no OpenShift resources for cluster {cluster}")
    return reasons


__all__ = [
    "DeprovisionProposalGenerator",
    "cluster_preconditions",
    "correlation_item",
    "pinned_git_revision",
    "InvalidProposalCommand",
    "InvalidProposalStatusCommand",
    "ProposalRefused",
    "PipelineProposalService",
    "ProposalService",
    "ProposalStatusService",
    "UnauthorizedProposalCommand",
    "UnauthorizedProposalStatusCommand",
]
