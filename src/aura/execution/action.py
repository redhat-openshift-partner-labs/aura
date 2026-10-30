"""The action subset of a proposal (Phase 3 Action Subset, E4).

An approval signs `action_digest`, and the guard compares it with the
digest of a proposal regenerated at execution time. The subset holds only
what an adapter would apply, so fields that change on every regeneration
leave it unchanged.
"""

from __future__ import annotations

import copy
from typing import Any

from aura.canonical import canonical_digest
from aura.proposals import Proposal


AURA_ANNOTATION_PREFIX = "aura.openshiftpartnerlabs.com/"
_TRAILER = "Aura-Proposal:"

ACTION_FIELDS = (
    "proposal_type",
    "cluster_name",
    "trigger_effect",
    "commit_message",
    "patch",
    "scaffold_commit",
    "pipeline_run",
)
# Fields that change on every regeneration, or that describe the action
# for a reader; the approver signs the stored proposal's digest as well.
EXCLUDED_FIELDS = (
    "proposal_id",
    "command_id",
    "created_at",
    "status",
    "base_commit",
    "evidence_snapshot_id",
    "evidence_snapshot_digest",
    "cwa_snapshot_id",
    "cwa_trace_id",
    "scaffold_run",
    "patch_check",
    "generator",
    "generator_version",
    "summary",
    "expected_effect",
    "risks",
    "reversibility",
    "rollback",
    "proposal_digest",
)


def action_digest(proposal: Proposal) -> str:
    fields = proposal.model_dump(mode="json", include=set(ACTION_FIELDS))
    fields["trigger_effect"] = sorted(fields["trigger_effect"])
    if fields["commit_message"] is not None:
        fields["commit_message"] = _without_trailer(fields["commit_message"])
    if fields["pipeline_run"] is not None:
        fields["pipeline_run"] = _without_aura_naming(fields["pipeline_run"])
    return canonical_digest(fields)


def _without_trailer(message: str) -> str:
    """Drop the `Aura-Proposal:` line, which names the regenerated ID."""

    return "".join(
        line
        for line in message.splitlines(keepends=True)
        if not line.startswith(_TRAILER)
    )


def _without_aura_naming(manifest: dict[str, Any]) -> dict[str, Any]:
    manifest = copy.deepcopy(manifest)
    metadata = manifest.get("metadata", {})
    metadata.pop("name", None)
    metadata.pop("generateName", None)
    annotations = metadata.get("annotations")
    if isinstance(annotations, dict):
        kept = {
            key: value
            for key, value in annotations.items()
            if not key.startswith(AURA_ANNOTATION_PREFIX)
        }
        # Only Aura's annotations leave no map, as if none were present.
        if kept:
            metadata["annotations"] = kept
        else:
            metadata.pop("annotations")
    return manifest


__all__ = [
    "ACTION_FIELDS",
    "AURA_ANNOTATION_PREFIX",
    "EXCLUDED_FIELDS",
    "action_digest",
]
