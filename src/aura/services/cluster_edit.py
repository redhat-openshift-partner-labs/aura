"""Deterministic metadata.yaml backfill proposals for one live cluster."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, final
from uuid import UUID, uuid5

import yaml

from aura.contracts import EvidenceItem
from aura.cwa import CWASnapshot
from aura.proposals import (
    EXPECTED_REVERSIBILITY,
    EXPECTED_TRIGGER_EFFECTS,
    METADATA_KEYS,
    SKIP_PROVISION,
    Proposal,
    ProposalType,
    added_file_patch,
    git_blob_abbrev,
    tier_metadata_keys,
)
from aura.services.diagnosis import cwa_evidence_items
from aura.services.proposal import (
    PatchCheck,
    check_patch,
    ProposalRefused,
    cluster_preconditions,
    correlation_item,
    pinned_git_revision,
)


FLEET_DEFAULTS_COMMIT = "9464f0b"
METADATA_DEFAULTS = dict(
    zip(METADATA_KEYS, ("aws", "legacy", "htpasswd", "v3"), strict=True)
)
RUN_KEYS = ("htpasswd-provider-name", "openshift-ai-version")
POST_PROVISION = "post-provision"
SCOPED_TEMPLATE = "crossplane-scoped"
REDACTED = "[REDACTED]"


@dataclass(frozen=True)
class _Value:
    value: str
    source: str


@final
class ClusterEditProposalGenerator:
    """Proposes the metadata.yaml keys a provision/<id> definition lacks."""

    provider_name = "aura"
    generator = "cluster-edit-deterministic"
    generator_version = "0.1.0"

    def __init__(self, *, patch_check: PatchCheck) -> None:
        self._patch_check = patch_check

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
        reasons = cluster_preconditions(correlation, items_by_id, cluster)
        base_commit = pinned_git_revision(snapshot)
        if base_commit is None:
            reasons.append("no pinned Git base commit in evidence")
        definition = _single_definition(correlation, items_by_id)
        if definition is not None:
            reasons.extend(_definition_reasons(definition, cluster))
        current = (
            str(definition.data.get("metadata_text") or "")
            if definition is not None
            and definition.kind == "cluster_definition"
            else None
        )
        if current is not None and "\r" in current:
            reasons.append(f"metadata.yaml for {cluster} has CR line endings")
        if reasons or definition is None:
            raise ProposalRefused(reasons)

        existing = _mapping(current)
        openshift_id = str(correlation.data.get("openshift_evidence_id"))
        openshift = items_by_id.get(openshift_id)
        tier, conflicts = _tier(definition, openshift, cluster)
        keys = tier_metadata_keys(tier)
        values = _metadata_values(
            definition,
            openshift,
            _post_provision_run(correlation, items_by_id),
            existing,
            cluster,
            keys,
            conflicts,
        )
        if conflicts:
            raise ProposalRefused(conflicts)
        missing = [key for key in keys if key not in existing]
        if not missing:
            raise ProposalRefused(
                [
                    f"metadata.yaml for {cluster} already declares every key "
                    f"tier {tier} needs"
                ]
            )
        path = f"provision/{cluster}/metadata.yaml"
        updated = _extended(
            current,
            "".join(_yaml_line(key, values[key].value) for key in missing),
        )
        expected = {**existing, **{key: values[key].value for key in missing}}
        if _parsed(updated) != expected:
            raise ProposalRefused(
                [
                    f"metadata.yaml for {cluster} cannot be extended by "
                    "appending keys"
                ]
            )

        patch = _patch(path, current, updated)
        patch_check = check_patch(
            self._patch_check,
            base_commit=str(base_commit),
            patch=patch,
            cluster_name=cluster,
            require_definition=True,
        )

        proposal_id = uuid5(
            snapshot.command_id,
            f"proposal:{snapshot.snapshot_id}",
        )
        proposal_type = ProposalType.CLUSTER_EDIT
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
            patch=patch,
            patch_check=patch_check,
            **_cluster_edit_text(
                cluster,
                path,
                adds_file=current is None,
                declared=keys,
                keys=[(key, values[key]) for key in missing],
                proposal_id=proposal_id,
            ),
        )


def _single_definition(
    correlation: EvidenceItem,
    items_by_id: dict[str, EvidenceItem],
) -> EvidenceItem | None:
    git_ids = correlation.data.get("git_evidence_ids", [])
    return items_by_id.get(git_ids[0]) if len(git_ids) == 1 else None


def _definition_reasons(definition: EvidenceItem, cluster: str) -> list[str]:
    error = definition.data.get("error") or definition.data.get(
        "remaining_files_error"
    )
    if error:
        return [f"definition for {cluster} is invalid: {error}"]
    others = [
        name
        for name in definition.data.get("missing_files", [])
        if name != "metadata.yaml"
    ]
    if others:
        return [
            f"definition for {cluster} is missing files other than "
            f"metadata.yaml: {others}"
        ]
    return []


def _mapping(text: str | None) -> dict[str, Any]:
    document = yaml.safe_load(text) if text is not None else None
    return document if isinstance(document, dict) else {}


def _parsed(text: str) -> Any:
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError:
        return None


def _post_provision_run(
    correlation: EvidenceItem,
    items_by_id: dict[str, EvidenceItem],
) -> EvidenceItem | None:
    runs = [
        items_by_id[run_id]
        for run_id in correlation.data.get("pipeline_run_evidence_ids", [])
        if run_id in items_by_id
        and items_by_id[run_id].data.get("pipeline_name") == POST_PROVISION
    ]
    return runs[0] if runs else None


def _default(key: str) -> _Value:
    return _Value(
        METADATA_DEFAULTS[key],
        f"fleet {FLEET_DEFAULTS_COMMIT} default",
    )


def _tier(
    definition: EvidenceItem,
    openshift: EvidenceItem | None,
    cluster: str,
) -> tuple[object, list[str]]:
    """Return the Git tier, refusing when OpenShift labels a different one."""

    tier = definition.data.get("tier")
    labels = openshift.data.get("managed_cluster_labels") if openshift else None
    actual = (labels or {}).get("tier")
    if actual is not None and actual != tier:
        return tier, [
            f"tier evidence conflicts for {cluster}: ManagedCluster patch "
            f"{tier}, OpenShift {actual}"
        ]
    return tier, []


def _metadata_values(
    definition: EvidenceItem,
    openshift: EvidenceItem | None,
    run: EvidenceItem | None,
    existing: dict[str, Any],
    cluster: str,
    keys: tuple[str, ...],
    conflicts: list[str],
) -> dict[str, _Value]:
    values = {
        "platform": _platform(
            definition, openshift, existing, cluster, conflicts
        ),
        "aws-credentials-mode": _credentials_mode(
            definition, existing, cluster, conflicts
        ),
    }
    for key in RUN_KEYS:
        if key in keys:
            values[key] = _run_value(key, run, existing, conflicts)
    return values


def _platform(
    definition: EvidenceItem,
    openshift: EvidenceItem | None,
    existing: dict[str, Any],
    cluster: str,
    conflicts: list[str],
) -> _Value:
    patch = definition.data.get("patch_platform") or None
    actual = openshift.data.get("cloud_provider") if openshift else None
    actual = actual or None
    if patch and actual and patch != actual:
        conflicts.append(
            f"platform evidence conflicts for {cluster}: "
            f"ClusterDeployment patch {patch}, OpenShift {actual}"
        )
    declared = existing.get("platform")
    if declared is not None and patch and declared != patch:
        conflicts.append(
            f"metadata.yaml declares platform {declared} but evidence "
            f"shows {patch}"
        )
    if not patch:
        # A valid definition always has one; refuse rather than guess.
        conflicts.append(
            f"no ClusterDeployment platform evidence for {cluster}"
        )
    return _Value(str(patch), "ClusterDeployment patch")


def _credentials_mode(
    definition: EvidenceItem,
    existing: dict[str, Any],
    cluster: str,
    conflicts: list[str],
) -> _Value:
    template = definition.data.get("crossplane_template")
    scoped = template == SCOPED_TEMPLATE
    declared = existing.get("aws-credentials-mode")
    if declared is None and scoped:
        conflicts.append(
            f"cluster {cluster} uses scoped AWS credentials but metadata.yaml "
            "does not declare aws-credentials-mode"
        )
    elif (
        declared is not None
        and template
        and (declared == "scoped") != scoped
    ):
        conflicts.append(
            f"metadata.yaml declares aws-credentials-mode {declared} but the "
            f"Crossplane template is {template}"
        )
    return _default("aws-credentials-mode")


def _run_value(
    key: str,
    run: EvidenceItem | None,
    existing: dict[str, Any],
    conflicts: list[str],
) -> _Value:
    params = run.data.get("params", {}) if run is not None else {}
    # A run of another tier never applied a tier-gated key to the spoke.
    if key not in params or key not in tier_metadata_keys(params.get("tier")):
        return _default(key)
    name = run.data.get("pipeline_run_name")
    used = params[key]
    if used == REDACTED:
        conflicts.append(f"PipelineRun {name} parameter {key} is redacted")
    elif key in existing and str(existing[key]) != str(used):
        conflicts.append(
            f"metadata.yaml declares {key} {existing[key]} but PipelineRun "
            f"{name} used {used}"
        )
    return _Value(str(used), f"PipelineRun {name}")


def _yaml_line(key: str, value: str) -> str:
    return yaml.safe_dump(
        {key: value},
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
        width=1000,
    )


def _extended(current: str | None, appended: str) -> str:
    if not current:
        return appended
    separator = "" if current.endswith("\n") else "\n"
    return current + separator + appended


def _patch(path: str, current: str | None, updated: str) -> str:
    """Return a Git patch that creates the file or appends to its end."""

    if current is None:
        return added_file_patch(path, updated)
    new_lines = updated.splitlines()
    # The whole file is context, so the patch shows it before and after.
    old_lines = current.splitlines()
    body = [f" {line}\n" for line in old_lines]
    if old_lines and not current.endswith("\n"):
        body[-1:] = [
            f"-{old_lines[-1]}\n",
            "\\ No newline at end of file\n",
            f"+{old_lines[-1]}\n",
        ]
    added = new_lines[len(old_lines):]
    return "".join(
        [
            f"diff --git a/{path} b/{path}\n",
            f"index {git_blob_abbrev(current)}..{git_blob_abbrev(updated)} "
            "100644\n",
            f"--- a/{path}\n",
            f"+++ b/{path}\n",
            f"@@ -1,{len(old_lines)} +1,{len(old_lines) + len(added)} @@\n",
            *body,
            *(f"+{line}\n" for line in added),
        ]
    )


def _cluster_edit_text(
    cluster: str,
    path: str,
    *,
    adds_file: bool,
    declared: tuple[str, ...],
    keys: list[tuple[str, _Value]],
    proposal_id: UUID,
) -> dict[str, object]:
    names = ", ".join(key for key, _ in keys)
    sources = "; ".join(
        f"{key}: {value.value} from {value.source}" for key, value in keys
    )
    action = (
        "Backfill metadata.yaml"
        if adds_file
        else "Add missing metadata.yaml keys"
    )
    if adds_file:
        body = (
            f"Add {path} with {names}. Push this commit alone, as the push "
            f"head commit, so {SKIP_PROVISION} suppresses pre-provision."
        )
        title = f"{action} for cluster {cluster} {SKIP_PROVISION}"
    else:
        body = (
            f"Add {names} to {path}. Only an existing file changes, so no "
            "pipeline starts."
        )
        title = f"{action} for cluster {cluster}"
    risks = [
        "Later provision and post-provision runs read these values from "
        "metadata.yaml instead of applying defaults.",
        f"Values from fleet {FLEET_DEFAULTS_COMMIT} defaults keep that "
        "value if fleet later changes a default.",
    ]
    if adds_file:
        risks.insert(
            0,
            f"Adding {path} starts pre-provision unless this commit is the "
            f"push head commit and keeps {SKIP_PROVISION}; with a merge "
            "commit, the merge commit is the head commit instead.",
        )
    return {
        "summary": f"{action} for cluster {cluster}. {sources}.",
        "expected_effect": (
            f"{path} declares {', '.join(declared)}. Each value a "
            "pipeline reads is what it uses today with the key absent, or "
            "what the latest post-provision run used; fleet "
            f"{FLEET_DEFAULTS_COMMIT} does not read platform."
        ),
        "commit_message": (
            f"{title}\n\n{body}\n\nAura-Proposal: {proposal_id}\n"
        ),
        "risks": risks,
        "rollback": (
            "Revert the commit. Reverting changes only metadata.yaml and "
            "starts no pipeline."
        ),
    }


__all__ = [
    "ClusterEditProposalGenerator",
    "FLEET_DEFAULTS_COMMIT",
    "METADATA_DEFAULTS",
]
