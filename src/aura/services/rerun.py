"""Deterministic post-provision rerun proposals copied from a prior run."""

from __future__ import annotations

from datetime import datetime
from typing import Any, final
from uuid import UUID, uuid5

import yaml

from aura.contracts import EvidenceItem
from aura.cwa import CWASnapshot
from aura.proposals import (
    EXPECTED_REVERSIBILITY,
    EXPECTED_TRIGGER_EFFECTS,
    RERUN_NAMESPACE,
    RERUN_PIPELINE,
    RERUN_PROPOSAL_ANNOTATION,
    RERUN_SOURCE_ANNOTATION,
    Proposal,
    ProposalType,
)
from aura.services.cluster_edit import (
    FLEET_DEFAULTS_COMMIT,
    METADATA_DEFAULTS,
    RUN_KEYS,
)
from aura.services.diagnosis import cwa_evidence_items
from aura.services.proposal import (
    ProposalRefused,
    cluster_preconditions,
    correlation_item,
)


FINISHED = frozenset({"Succeeded", "Failed"})
REDACTED = "[REDACTED]"
_WORKSPACE_SOURCES = {
    "volumeClaimTemplate": lambda binding: {"spec": binding["claim_spec"]},
    "persistentVolumeClaim": lambda binding: {
        "claimName": binding["reference"]
    },
    "secret": lambda binding: {"secretName": binding["reference"]},
    "configMap": lambda binding: {"name": binding["reference"]},
    "emptyDir": lambda binding: {},
}


@final
class PostProvisionRerunGenerator:
    """Proposes a PipelineRun that repeats a finished post-provision run."""

    provider_name = "aura"
    generator = "post-provision-rerun-deterministic"
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
        reasons = cluster_preconditions(correlation, items_by_id, cluster)
        openshift = items_by_id.get(
            str(correlation.data.get("openshift_evidence_id"))
        )
        if openshift is not None and not _provisioned(openshift):
            reasons.append(f"cluster {cluster} is not Provisioned")
        source = _source_run(correlation, items_by_id)
        if source is None:
            reasons.append(_missing_run_reason(correlation, cluster))
        definition = next(
            (item for item in items if item.kind == "pipeline_definition"),
            None,
        )
        if definition is None:
            reasons.append("no post-provision Pipeline definition in evidence")
        elif definition.data.get("malformed_params"):
            reasons.append(
                "the post-provision Pipeline has "
                f"{definition.data['malformed_params']} malformed parameters"
            )
        metadata, metadata_reason = _metadata(
            correlation, items_by_id, cluster
        )
        if metadata_reason:
            reasons.append(metadata_reason)
        if source is not None and definition is not None:
            reasons.extend(_run_reasons(source, definition, metadata))
        if reasons or source is None:
            raise ProposalRefused(reasons)

        proposal_id = uuid5(
            snapshot.command_id,
            f"proposal:{snapshot.snapshot_id}",
        )
        name = str(source.data["pipeline_run_name"])
        proposal_type = ProposalType.POST_PROVISION_RERUN
        return Proposal(
            proposal_id=proposal_id,
            command_id=snapshot.command_id,
            proposal_type=proposal_type,
            cluster_name=cluster,
            created_at=created_at,
            evidence_snapshot_id=snapshot.evidence_snapshot_id,
            evidence_snapshot_digest=snapshot.evidence_snapshot_digest,
            cwa_snapshot_id=snapshot.snapshot_id,
            cwa_trace_id=cwa_trace_id,
            generator=self.generator,
            generator_version=self.generator_version,
            trigger_effect=EXPECTED_TRIGGER_EFFECTS[proposal_type],
            reversibility=EXPECTED_REVERSIBILITY[proposal_type],
            pipeline_run=_manifest(cluster, source, proposal_id),
            summary=(
                f"Rerun post-provision for cluster {cluster} from "
                f"PipelineRun {name}."
            ),
            expected_effect=(
                "Phase 3 creates this PipelineRun through the Kubernetes "
                "API, and the post-provision tasks run again against the "
                f"spoke with {name}'s parameters, workspaces, and run "
                "template."
            ),
            risks=[
                "Post-provision tasks change the spoke cluster and cannot "
                "be undone.",
                f"Parameters are copied from {name}; values that changed "
                "since, such as dns-zones after a domain change, are not "
                "refreshed.",
                "Parameters the source run did not set take the Pipeline "
                "defaults; htpasswd-provider-name and openshift-ai-version "
                f"were checked assuming fleet {FLEET_DEFAULTS_COMMIT} "
                "defaults.",
                "Timeouts are not copied; Tekton's default applies.",
            ],
            rollback=(
                "Discard the proposal before the PipelineRun is created; "
                "once it starts, no rollback exists."
            ),
        )


def _provisioned(openshift: EvidenceItem) -> bool:
    return any(
        condition.get("type") == "Provisioned"
        and condition.get("status") == "True"
        for condition in openshift.data.get(
            "cluster_deployment_conditions", []
        )
    )


def _source_run(
    correlation: EvidenceItem,
    items_by_id: dict[str, EvidenceItem],
) -> EvidenceItem | None:
    return next(
        (
            items_by_id[run_id]
            for run_id in correlation.data.get("pipeline_run_evidence_ids", [])
            if run_id in items_by_id
            and items_by_id[run_id].data.get("pipeline_name") == RERUN_PIPELINE
        ),
        None,
    )


def _missing_run_reason(correlation: EvidenceItem, cluster: str) -> str:
    return next(
        (
            str(missing)
            for missing in correlation.data.get("missing_evidence", [])
            if str(missing).startswith("Selected Tekton PipelineRun")
        ),
        f"no post-provision PipelineRun for cluster {cluster}",
    )


def _metadata(
    correlation: EvidenceItem,
    items_by_id: dict[str, EvidenceItem],
    cluster: str,
) -> tuple[dict[str, Any], str | None]:
    git_ids = correlation.data.get("git_evidence_ids", [])
    definition = items_by_id.get(git_ids[0]) if len(git_ids) == 1 else None
    if definition is None:
        return {}, None
    if definition.kind == "cluster_definition":
        document = yaml.safe_load(definition.data.get("metadata_text") or "")
        return (document if isinstance(document, dict) else {}), None
    if "metadata.yaml" in definition.data.get("missing_files", []):
        return {}, None
    return {}, f"cannot read metadata.yaml for {cluster}"


def _run_reasons(
    source: EvidenceItem,
    definition: EvidenceItem,
    metadata: dict[str, Any],
) -> list[str]:
    run = f"PipelineRun {source.data['pipeline_run_name']}"
    reasons: list[str] = []
    if source.data.get("status") not in FINISHED:
        reasons.append(f"{run} has not finished")
    params: dict[str, Any] = source.data.get("params") or {}
    declared = {entry["name"] for entry in definition.data["params"]}
    required = definition.data["required_params"]
    missing = [name for name in required if name not in params]
    if missing:
        reasons.append(f"{run} lacks required parameters: {missing}")
    unknown = sorted(set(params) - declared)
    if unknown:
        reasons.append(
            f"{run} sets parameters the post-provision Pipeline does not "
            f"declare: {unknown}"
        )
    reasons.extend(
        f"{run} parameter {name} is redacted"
        for name, value in params.items()
        if value == REDACTED
    )
    for key in RUN_KEYS:
        used = str(params.get(key, METADATA_DEFAULTS[key]))
        expected = str(metadata.get(key, METADATA_DEFAULTS[key]))
        if used != expected:
            verb = "declares" if key in metadata else "defaults to"
            reasons.append(
                f"{run} would use {key} {used} but metadata.yaml "
                f"{verb} {expected}"
            )
    uncopyable = [
        *source.data.get("run_template", {}).get("unsupported_fields", []),
        *(
            f"workspaces.{binding['name']}.{field}"
            for binding in source.data.get("workspaces", [])
            for field in binding.get("unsupported_fields", [])
        ),
    ]
    if uncopyable:
        reasons.append(f"{run} has fields a rerun cannot copy: {uncopyable}")
    reasons.extend(
        f"{run} workspace {binding['name']} uses source "
        f"{binding['source']}, which a rerun cannot copy"
        for binding in source.data.get("workspaces", [])
        if binding["source"] not in _WORKSPACE_SOURCES
    )
    return reasons


def _manifest(
    cluster: str,
    source: EvidenceItem,
    proposal_id: UUID,
) -> dict[str, Any]:
    template = source.data.get("run_template") or {}
    run_template: dict[str, Any] = {}
    if template.get("service_account_name"):
        run_template["serviceAccountName"] = template["service_account_name"]
    if template.get("pod_template"):
        run_template["podTemplate"] = template["pod_template"]
    spec: dict[str, Any] = {
        "pipelineRef": {"name": RERUN_PIPELINE},
        "params": [
            {"name": name, "value": value}
            for name, value in (source.data.get("params") or {}).items()
        ],
        "workspaces": [
            _workspace(binding)
            for binding in source.data.get("workspaces", [])
        ],
    }
    if run_template:
        spec["taskRunTemplate"] = run_template
    return {
        "apiVersion": "tekton.dev/v1",
        "kind": "PipelineRun",
        "metadata": {
            "generateName": f"{RERUN_PIPELINE}-{cluster}-",
            "namespace": RERUN_NAMESPACE,
            "annotations": {
                RERUN_PROPOSAL_ANNOTATION: str(proposal_id),
                RERUN_SOURCE_ANNOTATION: str(
                    source.data["pipeline_run_name"]
                ),
            },
        },
        "spec": spec,
    }


def _workspace(binding: dict[str, Any]) -> dict[str, Any]:
    workspace: dict[str, Any] = {"name": binding["name"]}
    if binding.get("sub_path") is not None:
        workspace["subPath"] = binding["sub_path"]
    workspace[binding["source"]] = _WORKSPACE_SOURCES[binding["source"]](
        binding
    )
    return workspace


__all__ = ["PostProvisionRerunGenerator"]
