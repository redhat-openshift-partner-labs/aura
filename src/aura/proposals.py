"""Proposal contract for reviewable, non-executing Aura changes."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any
from uuid import UUID

import yaml
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
TIER_INDEPENDENT_METADATA_KEYS = (
    "platform",
    "aws-credentials-mode",
    "htpasswd-provider-name",
)
AI_METADATA_KEYS = ("openshift-ai-version",)
METADATA_KEYS = (*TIER_INDEPENDENT_METADATA_KEYS, *AI_METADATA_KEYS)
AI_TIER = "ai"
CLUSTER_NAME_PATTERN = r"^[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?$"
SCAFFOLD_NAME_MAX = 49
SCAFFOLD_CLOUD = "aws"
SCAFFOLD_BASE_PATH = "provision"
SCAFFOLD_TEMPLATES_DIR = "cluster-templates"
SCAFFOLD_OPERATOR_FLAGS = (
    "--topology",
    "--environment",
    "--base-domain",
    "--control-plane-type",
    "--worker-type",
    "--control-plane-replicas",
    "--worker-replicas",
    "--image-set",
    "--zones",
    "--install-attempts-limit",
    "--htpasswd-provider-name",
)
SCAFFOLD_AURA_FLAGS = (
    "--name",
    "--region",
    "--tier",
    "--base-path",
    "--cluster-templates-dir",
)
SCAFFOLD_FORBIDDEN_FLAGS = (
    "--force",
    "--skip-validation",
    "--legacy-mint-credentials",
)
SCAFFOLD_REQUIRED_FILES = (
    "crossplane/kustomization.yaml",
    "hive/patches/clusterdeployment.yaml",
    "hive/patches/managedcluster.yaml",
    "metadata.yaml",
)
_REGULAR_FILE_MODE = "100644"


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

RERUN_PROPOSAL_ANNOTATION = "aura.openshiftpartnerlabs.com/proposal-id"
RERUN_SOURCE_ANNOTATION = "aura.openshiftpartnerlabs.com/source-pipeline-run"
_RERUN_KEYS = ("apiVersion", "kind", "metadata", "spec")
_RERUN_METADATA_KEYS = ("generateName", "namespace", "annotations")
_RERUN_ANNOTATIONS = (RERUN_PROPOSAL_ANNOTATION, RERUN_SOURCE_ANNOTATION)
_RERUN_SPEC_KEYS = ("pipelineRef", "params", "workspaces", "taskRunTemplate")
_RUN_TEMPLATE_KEYS = ("serviceAccountName", "podTemplate")
_POD_TEMPLATE_KEYS = (
    "securityContext",
    "imagePullSecrets",
    "nodeSelector",
    "tolerations",
)
_WORKSPACE_SOURCES: dict[str, tuple[str, ...]] = {
    "volumeClaimTemplate": ("spec",),
    "persistentVolumeClaim": ("claimName",),
    "secret": ("secretName", "items"),
    "configMap": ("name",),
    "emptyDir": (),
}
_SECRET_ITEM_KEYS = ("key", "path", "mode")
_DIFF_HEADER = re.compile(r"^diff --git a/(\S+) b/(\S+)$")
_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+\d+(?:,\d+)? @@")
_NO_NEWLINE = "\\ No newline at end of file"


def deprovision_sentinel_patch(cluster_name: str) -> str:
    """Return the Git patch that adds an empty deprovision sentinel."""

    sentinel = f"deprovision/{cluster_name}{ARCHIVE_SUFFIX}"
    return (
        f"diff --git a/{sentinel} b/{sentinel}\n"
        "new file mode 100644\n"
        "index 0000000..e69de29\n"
    )


class ScaffoldRun(BaseModel):
    """How the pinned scaffold produced a scaffold proposal's patch."""

    model_config = ConfigDict(extra="forbid")

    kustomize_version: str = Field(min_length=1)
    arguments: list[str]
    exit_code: int
    stdout: str
    stderr: str
    files: list[str]


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
    scaffold_run: ScaffoldRun | None = None
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


def tier_metadata_keys(tier: object) -> tuple[str, ...]:
    """Return the metadata.yaml keys fleet reads for a cluster of tier.

    post-provision runs apply-ai-workloads, the only reader of
    openshift-ai-version, only when tier is exactly "ai".
    """

    return METADATA_KEYS if tier == AI_TIER else TIER_INDEPENDENT_METADATA_KEYS


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
    if (proposal.proposal_type is ProposalType.SCAFFOLD) != (
        proposal.scaffold_run is not None
    ):
        raise ValueError(
            "scaffold_run is required for scaffold proposals only"
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
    if proposal.proposal_type is ProposalType.SCAFFOLD:
        if kinds != {"added"}:
            raise ValueError("scaffold proposal may only add files")
        _validate_scaffold(proposal, prefix)
        return
    if kinds - {"added", "modified"}:
        raise ValueError(
            "cluster_edit proposal may not delete or rename files"
        )
    if proposal.proposal_type is ProposalType.CLUSTER_EDIT:
        _validate_metadata_edit(proposal.patch or "", f"{prefix}metadata.yaml")


def _validate_scaffold(proposal: Proposal, prefix: str) -> None:
    """Check a scaffold patch and the run that produced it."""

    if len(proposal.cluster_name) > SCAFFOLD_NAME_MAX:
        raise ValueError(
            f"scaffold cluster_name must be at most {SCAFFOLD_NAME_MAX} "
            "characters"
        )
    added = _added_files(proposal.patch or "")
    if any(mode != _REGULAR_FILE_MODE for mode, _ in added.values()):
        raise ValueError(
            "scaffold proposal may only add regular files with mode "
            f"{_REGULAR_FILE_MODE}"
        )
    for name in SCAFFOLD_REQUIRED_FILES:
        if f"{prefix}{name}" not in added:
            raise ValueError(f"scaffold proposal must add {prefix}{name}")
    metadata = _yaml_mapping(added[f"{prefix}metadata.yaml"][1])
    if (
        metadata.get("platform") != SCAFFOLD_CLOUD
        or metadata.get("aws-credentials-mode") != "scoped"
    ):
        raise ValueError(
            "scaffold metadata.yaml must declare platform aws and "
            "aws-credentials-mode scoped"
        )
    run = proposal.scaffold_run
    if run is None:
        raise ValueError(
            "scaffold_run is required for scaffold proposals only"
        )
    if run.exit_code != 0:
        raise ValueError("scaffold_run exit_code must be 0")
    if run.files != sorted(added):
        raise ValueError(
            "scaffold_run files must list exactly the files the patch adds, "
            "sorted"
        )
    _validate_scaffold_arguments(run.arguments, proposal.cluster_name)


def _added_files(patch: str) -> dict[str, tuple[str, list[str]]]:
    """Map each added file to its mode and content lines."""

    added: dict[str, tuple[str, list[str]]] = {}
    for block in re.split(r"(?m)^(?=diff --git )", patch):
        lines = block.splitlines()
        modes = [
            line.removeprefix("new file mode ")
            for line in lines
            if line.startswith("new file mode ")
        ]
        header = _DIFF_HEADER.match(lines[0]) if lines else None
        if header is None or not modes:
            continue
        hunk = next(
            (
                index
                for index, line in enumerate(lines)
                if line.startswith("@@")
            ),
            len(lines),
        )
        content = [
            line[1:] for line in lines[hunk + 1 :] if line.startswith("+")
        ]
        added[header.group(2)] = (modes[0], content)
    return added


def _yaml_mapping(lines: list[str]) -> dict[str, Any]:
    try:
        document = yaml.safe_load("\n".join(lines))
    except yaml.YAMLError:
        return {}
    return document if isinstance(document, dict) else {}


def _validate_scaffold_arguments(arguments: list[str], cluster: str) -> None:
    if not arguments or arguments[0] != SCAFFOLD_CLOUD:
        raise ValueError(
            f"scaffold_run arguments must start with {SCAFFOLD_CLOUD}"
        )
    allowed = {*SCAFFOLD_OPERATOR_FLAGS, *SCAFFOLD_AURA_FLAGS}
    values: dict[str, str] = {}
    index = 1
    while index < len(arguments):
        flag = arguments[index]
        if not flag.startswith("--"):
            raise ValueError(f"scaffold_run arguments pass stray value {flag}")
        if flag in SCAFFOLD_FORBIDDEN_FLAGS:
            raise ValueError(f"scaffold_run arguments must not pass {flag}")
        if flag not in allowed:
            raise ValueError(
                f"scaffold_run arguments pass unsupported flag {flag}"
            )
        if flag in values:
            raise ValueError(f"scaffold_run arguments repeat {flag}")
        value = arguments[index + 1] if index + 1 < len(arguments) else None
        if value is None or value.startswith("--"):
            raise ValueError(f"scaffold_run arguments give {flag} no value")
        values[flag] = value
        index += 2
    for flag, expected in (
        ("--name", cluster),
        ("--base-path", SCAFFOLD_BASE_PATH),
        ("--cluster-templates-dir", SCAFFOLD_TEMPLATES_DIR),
    ):
        if values.get(flag) != expected:
            raise ValueError(
                f"scaffold_run arguments must pass {flag} {expected}"
            )


def _validate_metadata_edit(patch: str, metadata: str) -> None:
    """Require a whole-file edit that only appends missing metadata keys.

    A modify patch must be one hunk from line 1 with no trailing context,
    which Git anchors to the whole file, so the patch alone shows the
    file before and after the change.
    """

    changes = patch_changes(patch)
    others = [path for path, _ in changes if path != metadata]
    if others:
        raise ValueError(
            f"cluster_edit proposal may change only {metadata}: {others}"
        )
    hunks = _hunks(patch)
    if changes[0][1] == "added":
        existing: list[str] = []
        appended = [
            line[1:]
            for hunk in hunks
            for line in hunk[1:]
            if line.startswith("+")
        ]
    else:
        existing, appended = _appended_lines(hunks)
    before = _metadata_mapping(existing)
    added = _metadata_mapping(appended)
    unknown = sorted(set(added) - set(METADATA_KEYS))
    if unknown:
        raise ValueError(
            "cluster_edit proposal may only add the metadata keys fleet "
            f"reads: {unknown}"
        )
    repeated = sorted(set(added) & set(before))
    if repeated:
        raise ValueError(
            "cluster_edit proposal may only add keys metadata.yaml lacks: "
            f"{repeated}"
        )
    after = _metadata_mapping([*existing, *appended])
    if after != {**before, **added}:
        raise ValueError(
            "cluster_edit append changes how metadata.yaml parses"
        )
    if set(TIER_INDEPENDENT_METADATA_KEYS) - set(after):
        raise ValueError(
            "metadata.yaml must declare "
            f"{', '.join(TIER_INDEPENDENT_METADATA_KEYS)}"
        )


def _hunks(patch: str) -> list[list[str]]:
    hunks: list[list[str]] = []
    for line in patch.splitlines():
        if line.startswith("@@"):
            hunks.append([line])
        elif hunks:
            hunks[-1].append(line)
    return hunks


def _appended_lines(hunks: list[list[str]]) -> tuple[list[str], list[str]]:
    header = _HUNK_HEADER.match(hunks[0][0]) if len(hunks) == 1 else None
    if header is None or header.group(1) != "1":
        raise ValueError(
            "cluster_edit proposal must change metadata.yaml in one hunk "
            "that starts at line 1"
        )
    existing: list[str] = []
    appended: list[str] = []
    body = hunks[0][1:]
    ended = False
    index = 0
    while index < len(body):
        line = body[index]
        if line.startswith(" ") and not ended:
            existing.append(line[1:])
        elif line.startswith("-") and not ended:
            # Only an unterminated last line may be re-added with a newline.
            if body[index + 1 : index + 3] != [_NO_NEWLINE, f"+{line[1:]}"]:
                raise ValueError(
                    "cluster_edit proposal may not change existing lines in "
                    "metadata.yaml"
                )
            existing.append(line[1:])
            ended = True
            index += 2
        elif line.startswith("+"):
            appended.append(line[1:])
            ended = True
        else:
            raise ValueError(
                "cluster_edit proposal may only append to the end of "
                "metadata.yaml"
            )
        index += 1
    if len(existing) != int(header.group(2) or 1):
        raise ValueError(
            "cluster_edit proposal must change metadata.yaml in one hunk "
            "that starts at line 1"
        )
    return existing, appended


def _metadata_mapping(lines: list[str]) -> dict[str, Any]:
    try:
        document = yaml.safe_load("\n".join(lines)) if lines else {}
    except yaml.YAMLError as error:
        raise ValueError(
            "metadata.yaml in a cluster_edit patch must be a YAML mapping"
        ) from error
    if document is None:
        return {}
    if not isinstance(document, dict):
        raise ValueError(
            "metadata.yaml in a cluster_edit patch must be a YAML mapping"
        )
    return document


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
    _require_only(manifest, _RERUN_KEYS, "pipeline_run")
    _require_only(metadata, _RERUN_METADATA_KEYS, "pipeline_run metadata")
    _require_only(spec, _RERUN_SPEC_KEYS, "pipeline_run spec")
    _validate_rerun_shape(metadata, spec)
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
    prefix = f"{RERUN_PIPELINE}-{proposal.cluster_name}-"
    if metadata.get("generateName") != prefix:
        raise ValueError(f"pipeline_run generateName must be {prefix}")
    names = [str(param.get("name", "")) for param in spec.get("params", [])]
    repeated = sorted({name for name in names if names.count(name) > 1})
    if repeated:
        raise ValueError(
            f"pipeline_run parameter names must be unique: {repeated}"
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
        _without_named_secrets(manifest)
    ).redacted_fields:
        raise ValueError("pipeline_run must not contain secret material")


def _require_only(
    value: dict[str, Any],
    allowed: tuple[str, ...],
    label: str,
) -> None:
    if set(value) - set(allowed):
        raise ValueError(f"{label} may contain only {', '.join(allowed)}")


def _validate_rerun_shape(
    metadata: dict[str, Any],
    spec: dict[str, Any],
) -> None:
    """Mirror the fields a rerun copies, so any producer is held to them."""

    _require_only(
        metadata.get("annotations") or {},
        _RERUN_ANNOTATIONS,
        "pipeline_run annotations",
    )
    _require_only(
        spec.get("pipelineRef") or {}, ("name",), "pipeline_run pipelineRef"
    )
    template = spec.get("taskRunTemplate") or {}
    _require_only(template, _RUN_TEMPLATE_KEYS, "pipeline_run taskRunTemplate")
    _require_only(
        template.get("podTemplate") or {},
        _POD_TEMPLATE_KEYS,
        "pipeline_run podTemplate",
    )
    for workspace in spec.get("workspaces", []):
        _validate_workspace(workspace)


def _validate_workspace(workspace: dict[str, Any]) -> None:
    label = f"pipeline_run workspace {workspace.get('name')}"
    sources = [key for key in workspace if key in _WORKSPACE_SOURCES]
    if len(sources) != 1:
        raise ValueError(f"{label} needs exactly one supported source")
    source = sources[0]
    if set(workspace) - {"name", "subPath", source}:
        raise ValueError(
            f"{label} may contain only name, subPath, and its source"
        )
    sub_path = workspace.get("subPath")
    if sub_path is not None and (
        not isinstance(sub_path, str)
        or sub_path.startswith("/")
        or ".." in sub_path.split("/")
    ):
        raise ValueError(
            f"{label} subPath must be a relative path inside the workspace"
        )
    details = workspace[source]
    if source == "secret":
        _validate_named_secret(details)
    elif not isinstance(details, dict):
        raise ValueError(f"{label} {source} must be a mapping")
    else:
        _require_only(details, _WORKSPACE_SOURCES[source], f"{label} {source}")


def _validate_named_secret(details: object) -> None:
    if not isinstance(details, dict) or set(details) - set(
        _WORKSPACE_SOURCES["secret"]
    ):
        raise ValueError(
            "workspace secret bindings may hold only secretName and items"
        )
    name = details.get("secretName")
    if not isinstance(name, str) or not name:
        raise ValueError(
            "workspace secret bindings need a non-empty secretName"
        )
    for item in details.get("items", []):
        if not isinstance(item, dict) or set(item) - set(_SECRET_ITEM_KEYS):
            raise ValueError(
                "workspace secret items may hold only key, path, and mode"
            )


def _without_named_secrets(manifest: dict[str, Any]) -> dict[str, Any]:
    """Drop validated workspace Secret bindings before the redaction check.

    The redaction check would flag their `secret` key, but after
    _validate_named_secret such a binding holds only a Secret's name and
    key-to-path items, not its content.
    """

    spec = manifest.get("spec") or {}
    workspaces = [
        {key: item for key, item in workspace.items() if key != "secret"}
        for workspace in spec.get("workspaces", [])
    ]
    return {**manifest, "spec": {**spec, "workspaces": workspaces}}


__all__ = [
    "CLUSTER_NAME_PATTERN",
    "RERUN_PROPOSAL_ANNOTATION",
    "RERUN_SOURCE_ANNOTATION",
    "METADATA_KEYS",
    "TIER_INDEPENDENT_METADATA_KEYS",
    "EXPECTED_REVERSIBILITY",
    "EXPECTED_TRIGGER_EFFECTS",
    "GIT_PROPOSAL_TYPES",
    "Proposal",
    "ProposalStatus",
    "ProposalType",
    "Reversibility",
    "ScaffoldRun",
    "SCAFFOLD_AURA_FLAGS",
    "SCAFFOLD_FORBIDDEN_FLAGS",
    "SCAFFOLD_NAME_MAX",
    "SCAFFOLD_OPERATOR_FLAGS",
    "SCAFFOLD_REQUIRED_FILES",
    "SKIP_DEPROVISION",
    "SKIP_PROVISION",
    "TriggerEffect",
    "compute_proposal_digest",
    "deprovision_sentinel_patch",
    "patch_changes",
    "tier_metadata_keys",
]
