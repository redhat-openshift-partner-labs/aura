"""Proposal contract for reviewable, non-executing Aura changes."""

from __future__ import annotations

import re
from collections import Counter
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from aura.canonical import canonical_digest
from aura.contracts import ARCHIVE_SUFFIX, reject_blank_entries
from aura.redaction import redact, redact_parameters


SKIP_PROVISION = "[skip-provision]"
SKIP_DEPROVISION = "[skip-deprovision]"
RERUN_NAMESPACE = "openshift-pipelines"
RERUN_PIPELINE = "post-provision"
CLUSTER_NAME_PATTERN = r"^[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?$"


class ProposalType(StrEnum):
    """Proposal families approved for Phase 2."""

    SCAFFOLD = "scaffold"
    DEPROVISION = "deprovision"
    POST_PROVISION_RERUN = "post_provision_rerun"
    CLUSTER_EDIT = "cluster_edit"


class ProposalStatus(StrEnum):
    """Proposal record states; Phase 2 only creates ready proposals."""

    PROPOSAL_READY = "proposal_ready"


class TriggerEffect(StrEnum):
    """Pipelines or Actions that merging or applying a proposal starts."""

    PRE_PROVISION = "pre-provision"
    PROVISION = "provision"
    POST_PROVISION = "post-provision"
    DEPROVISION = "deprovision"
    ARCHIVE_ACTION = "archive-cluster"


class Reversibility(StrEnum):
    """How far a proposal's effect can be undone."""

    REVERSIBLE = "reversible"
    REVERSIBLE_BEFORE_MERGE = "reversible_before_merge"
    IRREVERSIBLE = "irreversible"


GIT_PROPOSAL_TYPES = frozenset(
    {
        ProposalType.SCAFFOLD,
        ProposalType.DEPROVISION,
        ProposalType.CLUSTER_EDIT,
    }
)

EXPECTED_TRIGGER_EFFECTS: dict[ProposalType, list[TriggerEffect]] = {
    ProposalType.SCAFFOLD: [
        TriggerEffect.PRE_PROVISION,
        TriggerEffect.PROVISION,
        TriggerEffect.POST_PROVISION,
    ],
    ProposalType.DEPROVISION: [
        TriggerEffect.DEPROVISION,
        TriggerEffect.ARCHIVE_ACTION,
    ],
    ProposalType.CLUSTER_EDIT: [],
    ProposalType.POST_PROVISION_RERUN: [TriggerEffect.POST_PROVISION],
}

EXPECTED_REVERSIBILITY: dict[ProposalType, Reversibility] = {
    ProposalType.SCAFFOLD: Reversibility.REVERSIBLE_BEFORE_MERGE,
    ProposalType.DEPROVISION: Reversibility.REVERSIBLE_BEFORE_MERGE,
    ProposalType.CLUSTER_EDIT: Reversibility.REVERSIBLE,
    ProposalType.POST_PROVISION_RERUN: Reversibility.IRREVERSIBLE,
}

_DIFF_HEADER = re.compile(r"^diff --git a/(\S+) b/(\S+)$")


def deprovision_sentinel_patch(cluster_name: str) -> str:
    """Return the Git patch that adds an empty deprovision sentinel."""

    sentinel = f"deprovision/{cluster_name}{ARCHIVE_SUFFIX}"
    return (
        f"diff --git a/{sentinel} b/{sentinel}\n"
        "new file mode 100644\n"
        "index 0000000..e69de29\n"
    )


class Proposal(BaseModel):
    """Immutable, evidence-linked proposal that Phase 2 never executes."""

    model_config = ConfigDict(extra="forbid")

    proposal_id: UUID
    command_id: UUID
    proposal_type: ProposalType
    cluster_name: str = Field(pattern=CLUSTER_NAME_PATTERN)
    status: ProposalStatus = ProposalStatus.PROPOSAL_READY
    created_at: AwareDatetime
    base_commit: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    evidence_snapshot_id: UUID
    evidence_snapshot_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    cwa_snapshot_id: UUID
    cwa_trace_id: UUID
    generator: str = Field(min_length=1)
    generator_version: str = Field(min_length=1)
    scaffold_commit: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{40}$"
    )
    summary: str = Field(min_length=1)
    expected_effect: str = Field(min_length=1)
    trigger_effect: list[TriggerEffect]
    commit_message: str | None = None
    risks: list[str] = Field(min_length=1)
    reversibility: Reversibility
    rollback: str = Field(min_length=1)
    patch: str | None = None
    pipeline_run: dict[str, Any] | None = None
    proposal_digest: str = Field(default="")

    @field_validator(
        "generator",
        "generator_version",
        "summary",
        "expected_effect",
        "rollback",
    )
    @classmethod
    def reject_blank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value

    @field_validator("risks")
    @classmethod
    def reject_blank_risks(cls, value: list[str]) -> list[str]:
        return reject_blank_entries(value, "risks")

    @model_validator(mode="after")
    def validate_proposal(self) -> "Proposal":
        _validate_type_rules(self)
        if self.proposal_type in GIT_PROPOSAL_TYPES:
            _validate_git_proposal(self)
        else:
            _validate_rerun_proposal(self)
        computed_digest = compute_proposal_digest(self)
        if not self.proposal_digest:
            self.proposal_digest = computed_digest
        elif self.proposal_digest != computed_digest:
            raise ValueError("proposal digest does not match proposal")
        return self


def compute_proposal_digest(proposal: Proposal) -> str:
    """Return a canonical SHA-256 digest over every other proposal field."""

    return canonical_digest(
        proposal.model_dump(mode="json", exclude={"proposal_digest"})
    )


def patch_changes(patch: str) -> list[tuple[str, str]]:
    """Return each file in a Git patch as (path, added|modified|deleted|renamed)."""

    changes: list[tuple[str, str]] = []
    for block in re.split(r"(?m)^(?=diff --git )", patch):
        if not block.strip():
            continue
        lines = block.splitlines()
        header = _DIFF_HEADER.match(lines[0])
        if header is None:
            raise ValueError(f"unsupported patch header: {lines[0]}")
        old_path, new_path = header.groups()
        if any(line.startswith("new file mode") for line in lines):
            changes.append((new_path, "added"))
        elif any(line.startswith("deleted file mode") for line in lines):
            changes.append((old_path, "deleted"))
        elif old_path != new_path or any(
            line.startswith("rename from") for line in lines
        ):
            changes.append((new_path, "renamed"))
        else:
            changes.append((new_path, "modified"))
    return changes


def _validate_type_rules(proposal: Proposal) -> None:
    expected_effect = EXPECTED_TRIGGER_EFFECTS[proposal.proposal_type]
    if proposal.trigger_effect != expected_effect:
        raise ValueError(
            f"trigger_effect must be {[str(item) for item in expected_effect]} "
            f"for {proposal.proposal_type.value} proposals"
        )
    expected_reversibility = EXPECTED_REVERSIBILITY[proposal.proposal_type]
    if proposal.reversibility is not expected_reversibility:
        raise ValueError(
            f"reversibility must be {expected_reversibility.value} "
            f"for {proposal.proposal_type.value} proposals"
        )
    if (proposal.proposal_type is ProposalType.SCAFFOLD) != (
        proposal.scaffold_commit is not None
    ):
        raise ValueError(
            "scaffold_commit is required for scaffold proposals only"
        )


def _validate_git_proposal(proposal: Proposal) -> None:
    kind = proposal.proposal_type.value
    if proposal.base_commit is None:
        raise ValueError(f"base_commit is required for {kind} proposals")
    if proposal.pipeline_run is not None:
        raise ValueError(f"{kind} proposals must not include pipeline_run")
    changes = patch_changes(proposal.patch or "")
    if not changes:
        raise ValueError("patch must contain at least one file")
    _validate_patch_scope(proposal, changes)
    _validate_commit_message(proposal, changes)


def _validate_patch_scope(
    proposal: Proposal,
    changes: list[tuple[str, str]],
) -> None:
    cluster = proposal.cluster_name
    if proposal.proposal_type is ProposalType.DEPROVISION:
        sentinel = f"deprovision/{cluster}{ARCHIVE_SUFFIX}"
        if changes != [(sentinel, "added")]:
            raise ValueError(
                f"deprovision proposal must add only {sentinel}"
            )
        if proposal.patch != deprovision_sentinel_patch(cluster):
            raise ValueError(
                f"deprovision proposal must add {sentinel} "
                "as an empty regular file"
            )
        return
    prefix = f"provision/{cluster}/"
    outside = [path for path, _ in changes if not path.startswith(prefix)]
    if outside:
        raise ValueError(f"patch changes files outside {prefix}: {outside}")
    kinds = {change for _, change in changes}
    if proposal.proposal_type is ProposalType.SCAFFOLD and kinds != {"added"}:
        raise ValueError("scaffold proposal may only add files")
    if kinds - {"added", "modified"}:
        raise ValueError(
            "cluster_edit proposal may not delete or rename files"
        )
    if proposal.proposal_type is ProposalType.CLUSTER_EDIT:
        _validate_metadata_edit(proposal.patch or "", f"{prefix}metadata.yaml")


def _validate_metadata_edit(patch: str, metadata: str) -> None:
    others = [path for path, _ in patch_changes(patch) if path != metadata]
    if others:
        raise ValueError(
            f"cluster_edit proposal may change only {metadata}: {others}"
        )
    removed: Counter[str] = Counter()
    added: Counter[str] = Counter()
    in_hunk = False
    for line in patch.splitlines():
        if line.startswith("diff --git "):
            in_hunk = False
        elif line.startswith("@@"):
            in_hunk = True
        elif in_hunk and line.startswith("-"):
            removed[line[1:]] += 1
        elif in_hunk and line.startswith("+"):
            added[line[1:]] += 1
    # A removed line re-added unchanged only gains a final newline.
    if removed - added:
        raise ValueError(
            "cluster_edit proposal may not change existing lines in "
            f"{metadata}"
        )


def _validate_commit_message(
    proposal: Proposal,
    changes: list[tuple[str, str]],
) -> None:
    message = proposal.commit_message or ""
    if proposal.cluster_name not in message:
        raise ValueError(
            f"commit_message must name cluster {proposal.cluster_name}"
        )
    adds_files = any(change == "added" for _, change in changes)
    if proposal.proposal_type is ProposalType.CLUSTER_EDIT:
        if adds_files and SKIP_PROVISION not in message:
            raise ValueError(
                f"cluster_edit commit_message must contain {SKIP_PROVISION} "
                "when the patch adds a file"
            )
        return
    marker = (
        SKIP_DEPROVISION
        if proposal.proposal_type is ProposalType.DEPROVISION
        else SKIP_PROVISION
    )
    if marker in message:
        raise ValueError(f"commit_message must not contain {marker}")


def _validate_rerun_proposal(proposal: Proposal) -> None:
    for field in ("base_commit", "commit_message", "patch"):
        if getattr(proposal, field) is not None:
            raise ValueError(
                f"post_provision_rerun proposals must not include {field}"
            )
    manifest = proposal.pipeline_run
    if manifest is None:
        raise ValueError("post_provision_rerun proposals require pipeline_run")
    spec = manifest.get("spec") or {}
    metadata = manifest.get("metadata") or {}
    if (
        manifest.get("kind") != "PipelineRun"
        or not str(manifest.get("apiVersion", "")).startswith("tekton.dev/")
        or (spec.get("pipelineRef") or {}).get("name") != RERUN_PIPELINE
    ):
        raise ValueError(
            "pipeline_run must reference the post-provision Pipeline"
        )
    if metadata.get("namespace") != RERUN_NAMESPACE or "name" in metadata:
        raise ValueError(
            f"pipeline_run must use generateName in {RERUN_NAMESPACE}"
        )
    params = {
        str(param.get("name", "")): param.get("value")
        for param in spec.get("params", [])
    }
    if params.get("cluster-name") != proposal.cluster_name:
        raise ValueError(
            f"cluster-name parameter must be {proposal.cluster_name}"
        )
    if redact_parameters(params).redacted_fields or redact(
        manifest
    ).redacted_fields:
        raise ValueError("pipeline_run must not contain secret material")


__all__ = [
    "CLUSTER_NAME_PATTERN",
    "EXPECTED_REVERSIBILITY",
    "EXPECTED_TRIGGER_EFFECTS",
    "GIT_PROPOSAL_TYPES",
    "Proposal",
    "ProposalStatus",
    "ProposalType",
    "Reversibility",
    "SKIP_DEPROVISION",
    "SKIP_PROVISION",
    "TriggerEffect",
    "compute_proposal_digest",
    "deprovision_sentinel_patch",
    "patch_changes",
]
