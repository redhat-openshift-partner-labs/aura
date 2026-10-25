"""Deterministic new-cluster scaffold proposals."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any, final
from uuid import UUID, uuid5

import yaml

from aura.contracts import ARCHIVE_SUFFIX, EvidenceItem
from aura.cwa import CWASnapshot
from aura.proposals import (
    CLUSTER_NAME_PATTERN,
    EXPECTED_REVERSIBILITY,
    EXPECTED_TRIGGER_EFFECTS,
    SCAFFOLD_NAME_MAX,
    SCAFFOLD_OPERATOR_FLAGS,
    Proposal,
    ProposalType,
    ScaffoldRun,
    added_file_patch,
)
from aura.redaction import redact
from aura.scaffold import ScaffoldRequest, ScaffoldResult, ScaffoldRunner
from aura.scaffold import infra_prefix
from aura.services.diagnosis import cwa_evidence_items
from aura.services.proposal import ProposalRefused, pinned_git_revision


TIERS = ("base", "virt", "ai")
TOPOLOGIES = ("standard", "compact", "sno")
DEFINITION_KINDS = frozenset(
    {
        "cluster_definition",
        "cluster_definition_incomplete",
        "cluster_definition_invalid",
    }
)
OPENSHIFT_KINDS = frozenset(
    {"cluster_actual_state", "cluster_actual_state_incomplete"}
)
REGULAR_FILE_MODE = "100644"
_STREAM_FIELD = re.compile(r"^(\s*)([^\s:=]+)(\s*[:=]\s*)(.*)$")


@final
class ScaffoldProposalGenerator:
    """Runs the pinned scaffold and proposes its overlay as a patch."""

    provider_name = "aura"
    generator = "scaffold-deterministic"
    generator_version = "0.1.0"

    def __init__(self, *, runner: ScaffoldRunner) -> None:
        self._runner = runner

    def generate(
        self,
        snapshot: CWASnapshot,
        *,
        cwa_trace_id: UUID,
        created_at: datetime,
    ) -> Proposal:
        items = cwa_evidence_items(snapshot)
        request = ScaffoldRequest.model_validate(
            _single(items, "scaffold_request").data
        )
        checkout = _single(items, "scaffold_checkout").data
        kustomize = _single(items, "kustomize").data.get("version")
        base_commit = pinned_git_revision(snapshot)
        cluster = request.cluster_name
        reasons = [*_request_reasons(request), *_name_reasons(cluster, items)]
        if kustomize is None:
            reasons.append("kustomize is not on PATH")
        if not checkout.get("clean"):
            reasons.append(
                "scaffold checkout has uncommitted changes to tracked files"
            )
        if base_commit is None:
            reasons.append("no pinned Git base commit in evidence")
        if reasons:
            raise ProposalRefused(reasons)

        scaffold_commit = str(checkout["commit"])
        arguments = scaffold_arguments(request)
        result = self._runner.run(
            scaffold_commit=scaffold_commit,
            base_commit=str(base_commit),
            arguments=arguments,
        )
        reasons = _output_reasons(result, cluster)
        if reasons:
            raise ProposalRefused(reasons)

        proposal_id = uuid5(
            snapshot.command_id,
            f"proposal:{snapshot.snapshot_id}",
        )
        proposal_type = ProposalType.SCAFFOLD
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
            scaffold_commit=scaffold_commit,
            scaffold_run=ScaffoldRun(
                kustomize_version=str(kustomize),
                arguments=arguments,
                exit_code=result.exit_code,
                stdout=_redact_stream(result.stdout),
                stderr=_redact_stream(result.stderr),
                files=sorted(result.files),
            ),
            trigger_effect=EXPECTED_TRIGGER_EFFECTS[proposal_type],
            reversibility=EXPECTED_REVERSIBILITY[proposal_type],
            patch="".join(
                added_file_patch(path, result.files[path][1])
                for path in sorted(result.files)
            ),
            **_scaffold_text(
                request,
                scaffold_commit=scaffold_commit,
                arguments=arguments,
                proposal_id=proposal_id,
            ),
        )


def scaffold_arguments(request: ScaffoldRequest) -> list[str]:
    """Return the scaffold arguments for a request, in a fixed order."""

    return [
        "aws",
        "--name",
        request.cluster_name,
        "--region",
        request.region,
        "--tier",
        request.tier,
        *(part for option in request.options for part in option),
        "--base-path",
        "provision",
        "--cluster-templates-dir",
        "cluster-templates",
    ]


def _single(items: list[EvidenceItem], kind: str) -> EvidenceItem:
    matches = [item for item in items if item.kind == kind]
    if len(matches) != 1:
        raise ProposalRefused([f"expected one {kind} item in evidence"])
    return matches[0]


def _request_reasons(request: ScaffoldRequest) -> list[str]:
    reasons: list[str] = []
    name = request.cluster_name
    if request.cloud != "aws":
        reasons.append(
            f"scaffold supports only cloud aws, not {request.cloud}"
        )
    if not re.fullmatch(CLUSTER_NAME_PATTERN, name):
        reasons.append(f"cluster name {name} is not a DNS-1123 label")
    elif len(name) > SCAFFOLD_NAME_MAX:
        reasons.append(
            f"cluster name {name} is {len(name)} characters; scoped AWS "
            f"names allow at most {SCAFFOLD_NAME_MAX}"
        )
    if request.tier not in TIERS:
        reasons.append(
            f"tier {request.tier} is not one of {', '.join(TIERS)}"
        )
    seen: set[str] = set()
    for flag, value in request.options:
        if flag not in SCAFFOLD_OPERATOR_FLAGS:
            reasons.append(f"scaffold option {flag} is not allowed")
        elif flag in seen:
            reasons.append(f"scaffold option {flag} is repeated")
        elif flag == "--topology" and value not in TOPOLOGIES:
            reasons.append(
                f"topology {value} is not one of {', '.join(TOPOLOGIES)}"
            )
        seen.add(flag)
    return reasons


def _name_reasons(cluster: str, items: list[EvidenceItem]) -> list[str]:
    """Refuse a name already in use, or one whose infra prefix is."""

    reasons: list[str] = []
    others: list[tuple[str, str]] = []
    for item in items:
        path = str(item.data.get("path", ""))
        if item.kind in DEFINITION_KINDS:
            name = Path(path).name
            if name == cluster:
                reasons.append(
                    f"cluster {cluster} already has a definition at {path}"
                )
            else:
                others.append((name, path))
        elif item.kind == "deprovision_sentinel":
            name = Path(path).name.removesuffix(ARCHIVE_SUFFIX)
            if name == cluster:
                reasons.append(f"deprovision sentinel already exists: {path}")
            else:
                others.append((name, path))
    openshift = sorted(
        {
            str(item.data.get("cluster_name"))
            for item in items
            if item.kind in OPENSHIFT_KINDS
        }
    )
    for name in openshift:
        if name == cluster:
            reasons.append(
                f"OpenShift resources already exist for cluster {cluster}"
            )
        else:
            others.append((name, f"OpenShift cluster {name}"))
    prefix = infra_prefix(cluster)
    reasons.extend(
        f"infra prefix {prefix} is also used by {source}"
        for name, source in sorted(others, key=lambda other: other[1])
        if infra_prefix(name) == prefix
    )
    return reasons


def _output_reasons(result: ScaffoldResult, cluster: str) -> list[str]:
    """Refuse output the contract or the fleet-clusters PR check rejects."""

    if result.exit_code != 0:
        lines = [line for line in result.stderr.splitlines() if line.strip()]
        detail = f": {lines[-1].strip()}" if lines else ""
        return [f"scaffold exited {result.exit_code}{detail}"]
    if not result.files:
        return ["scaffold wrote no files"]
    root = f"provision/{cluster}/"
    outside = sorted(path for path in result.files if not path.startswith(root))
    if outside:
        return [f"scaffold wrote outside {root}: {outside}"]
    reasons: list[str] = []
    for path, (mode, text) in sorted(result.files.items()):
        if mode != REGULAR_FILE_MODE:
            reasons.append(
                f"scaffold wrote a non-regular file {path} with mode {mode}"
            )
            continue
        fields = _secret_fields(text)
        if fields:
            reasons.append(
                f"scaffold output {path} contains secret-named fields: "
                f"{fields}"
            )
    metadata = _mapping(result.files.get(f"{root}metadata.yaml"))
    if (
        metadata.get("platform") != "aws"
        or metadata.get("aws-credentials-mode") != "scoped"
    ):
        reasons.append(
            "scaffold metadata.yaml must declare platform aws and "
            "aws-credentials-mode scoped"
        )
    reasons.extend(_render_reasons(result.files, cluster, root))
    return reasons


def _secret_fields(text: str) -> list[str]:
    try:
        documents = list(yaml.safe_load_all(text))
    except yaml.YAMLError:
        return []
    return sorted(
        field
        for document in documents
        for field in redact(document).redacted_fields
    )


def _mapping(file: tuple[str, str] | None) -> dict[str, Any]:
    try:
        document = yaml.safe_load(file[1]) if file else None
    except yaml.YAMLError:
        return {}
    return document if isinstance(document, dict) else {}


def _render_reasons(
    files: dict[str, tuple[str, str]],
    cluster: str,
    root: str,
) -> list[str]:
    """Mirror the render rules of fleet-clusters check_new_overlays.py."""

    prefix = infra_prefix(cluster)
    patches = f"{root}crossplane/patches/"
    old_markers = (
        f"instance-profile/{prefix}-*",
        f"s3:::{prefix}-*",
        f"user/{cluster}-*",
        f"role/{cluster}-*",
    )
    reasons = [
        f"{path} contains the old render pattern {marker}"
        for path, (_, text) in sorted(files.items())
        if path.startswith(patches)
        for marker in old_markers
        if marker in text
    ]
    policy = f"{patches}machine-api/policy.yaml"
    current = f"instance-profile/{prefix}-?????-worker-profile"
    if policy not in files:
        reasons.append(f"{policy} is missing")
    elif current not in files[policy][1]:
        reasons.append(f"{policy} lacks {current}")
    return reasons


def _redact_stream(text: str) -> str:
    """Redact `name: value` and `name=value` lines with secret names."""

    lines = []
    for line in text.splitlines(keepends=True):
        match = _STREAM_FIELD.match(line.rstrip("\n"))
        if match and redact({match.group(2): ""}).redacted_fields:
            ending = "\n" if line.endswith("\n") else ""
            line = f"{''.join(match.group(1, 2, 3))}[REDACTED]{ending}"
        lines.append(line)
    return "".join(lines)


def _scaffold_text(
    request: ScaffoldRequest,
    *,
    scaffold_commit: str,
    arguments: list[str],
    proposal_id: UUID,
) -> dict[str, object]:
    cluster = request.cluster_name
    topology = dict(request.options).get("--topology", "standard")
    path = f"provision/{cluster}/"
    return {
        "summary": (
            f"Scaffold {topology} AWS cluster {cluster} in {request.region}, "
            f"tier {request.tier}, with fleet-cluster-scaffold "
            f"{scaffold_commit}."
        ),
        "expected_effect": (
            f"Merging adds {path}, and the fleet pipelines provision "
            f"cluster {cluster} on the hub."
        ),
        "commit_message": (
            f"Provision cluster {cluster}\n\n"
            f"Add {path} generated by fleet-cluster-scaffold "
            f"{scaffold_commit} with: {' '.join(arguments)}. Merging starts "
            "pre-provision, provision, and post-provision.\n\n"
            f"Aura-Proposal: {proposal_id}\n"
        ),
        "risks": [
            "Merging starts pre-provision, provision, and post-provision, "
            f"which create cluster {cluster} and its AWS resources.",
            "The infra prefix was checked against fleet-clusters and "
            "OpenShift, not Crossplane IAM labels; fleet's pre-provision "
            "check-prefix-collision task checks those.",
            "The overlay reflects the scaffold commit and the "
            "cluster-templates at the base commit; regenerate it if either "
            "changes before merge.",
        ],
        "rollback": (
            "Discard the proposal before merge. After merge, only a "
            f"deprovision of {cluster} reverses it."
        ),
    }


__all__ = ["ScaffoldProposalGenerator", "scaffold_arguments"]
