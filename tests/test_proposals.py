from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from pydantic import ValidationError

from aura.proposals import (
    EXPECTED_TRIGGER_EFFECTS,
    Proposal,
    ProposalType,
    patch_changes,
)


BASE_COMMIT = "492bc8e07afddcd557441ddb7d72158de60ab8cb"
FULL_METADATA = (
    "platform: aws\naws-credentials-mode: legacy\n"
    "htpasswd-provider-name: htpasswd\nopenshift-ai-version: v3"
)
MISSING_KEYS = (
    "+platform: aws",
    "+aws-credentials-mode: legacy",
    "+openshift-ai-version: v3",
)
CLUSTER = "0171d46a-scopedaws"
METADATA_PATH = f"provision/{CLUSTER}/metadata.yaml"


def added_file(path: str, content: str = "", mode: str = "100644") -> str:
    lines = [
        f"diff --git a/{path} b/{path}",
        f"new file mode {mode}",
        "index 0000000..e69de29",
    ]
    if content:
        body = content.splitlines()
        lines += [
            "--- /dev/null",
            f"+++ b/{path}",
            f"@@ -0,0 +1,{len(body)} @@",
            *(f"+{line}" for line in body),
        ]
    return "\n".join(lines) + "\n"


def hunk_patch(path: str, *hunks: tuple[str, ...]) -> str:
    lines = [
        f"diff --git a/{path} b/{path}",
        "index 1111111..2222222 100644",
        f"--- a/{path}",
        f"+++ b/{path}",
    ]
    for hunk in hunks:
        lines += list(hunk)
    return "\n".join(lines) + "\n"


def modified_file(path: str) -> str:
    return "\n".join(
        [
            f"diff --git a/{path} b/{path}",
            "index 1111111..2222222 100644",
            f"--- a/{path}",
            f"+++ b/{path}",
            "@@ -1 +1 @@",
            "-platform: gcp",
            "+platform: aws",
        ]
    ) + "\n"


def appended_file(
    path: str,
    last_line: str,
    *added: str,
    newline_at_end: bool = True,
) -> str:
    lines = [
        f"diff --git a/{path} b/{path}",
        "index 1111111..2222222 100644",
        f"--- a/{path}",
        f"+++ b/{path}",
    ]
    if newline_at_end:
        lines += [f"@@ -1 +1,{len(added) + 1} @@", f" {last_line}"]
    else:
        lines += [
            f"@@ -1 +1,{len(added) + 1} @@",
            f"-{last_line}",
            "\\ No newline at end of file",
            f"+{last_line}",
        ]
    return "\n".join([*lines, *(f"+{line}" for line in added)]) + "\n"


def deleted_file(path: str) -> str:
    return "\n".join(
        [
            f"diff --git a/{path} b/{path}",
            "deleted file mode 100644",
            "index 1111111..0000000",
        ]
    ) + "\n"


def rerun_manifest(**params: Any) -> dict[str, Any]:
    return {
        "apiVersion": "tekton.dev/v1",
        "kind": "PipelineRun",
        "metadata": {
            "generateName": f"post-provision-{CLUSTER}-",
            "namespace": "openshift-pipelines",
        },
        "spec": {
            "pipelineRef": {"name": "post-provision"},
            "params": [
                {"name": name, "value": value}
                for name, value in {"cluster-name": CLUSTER, **params}.items()
            ],
        },
    }


def rerun_with(**updates: Any) -> dict[str, Any]:
    manifest = rerun_manifest(tier="base")
    for section, values in updates.items():
        manifest[section] = {**manifest.get(section, {}), **values}
    return manifest


SECRET_WORKSPACE = {"name": "keys", "secret": {"secretName": "keys"}}
AURA_ANNOTATIONS = {
    "aura.openshiftpartnerlabs.com/proposal-id": "x",
    "aura.openshiftpartnerlabs.com/source-pipeline-run": "y",
}


def rerun_workspace(**workspace: Any) -> dict[str, Any]:
    return rerun_with(spec={"workspaces": [{"name": "w", **workspace}]})


def rerun_pod(**pod_template: Any) -> dict[str, Any]:
    return rerun_with(
        spec={"taskRunTemplate": {"podTemplate": pod_template}}
    )


BASE_FIELDS: dict[str, Any] = {
    "proposal_id": UUID(int=70),
    "command_id": UUID(int=1),
    "cluster_name": CLUSTER,
    "created_at": datetime(2026, 1, 1, tzinfo=UTC),
    "evidence_snapshot_id": UUID(int=40),
    "evidence_snapshot_digest": "a" * 64,
    "cwa_snapshot_id": UUID(int=41),
    "cwa_trace_id": UUID(int=42),
    "generator": "aura",
    "generator_version": "0.1.0",
    "summary": "Request deprovision of the test cluster.",
    "expected_effect": "The cluster is deprovisioned and archived.",
    "risks": ["The cluster and its workloads are destroyed."],
    "rollback": "Discard the patch before merge; nothing reverses it after.",
}

SCAFFOLD_FILES = {
    "crossplane/kustomization.yaml": "resources: []",
    "hive/patches/clusterdeployment.yaml": "[]",
    "hive/patches/managedcluster.yaml": "[]",
    "kustomization.yaml": "resources:\n- crossplane/\n- hive/",
    "metadata.yaml": "platform: aws\naws-credentials-mode: scoped",
}


def scaffold_arguments(cluster: str = CLUSTER, *extra: str) -> list[str]:
    return [
        "aws",
        "--name",
        cluster,
        "--region",
        "us-east-1",
        "--tier",
        "base",
        *extra,
        "--base-path",
        "provision",
        "--cluster-templates-dir",
        "cluster-templates",
    ]


def scaffold_fields(
    cluster: str = CLUSTER,
    contents: dict[str, str] = SCAFFOLD_FILES,
    *,
    modes: dict[str, str] | None = None,
    **run: Any,
) -> dict[str, Any]:
    paths = {name: f"provision/{cluster}/{name}" for name in contents}
    return {
        "cluster_name": cluster,
        "commit_message": f"feat: provision {cluster}",
        "patch": "".join(
            added_file(paths[name], content, (modes or {}).get(name, "100644"))
            for name, content in contents.items()
        ),
        "scaffold_run": {
            "kustomize_version": "v5.8.1",
            "arguments": scaffold_arguments(cluster),
            "exit_code": 0,
            "stdout": "",
            "stderr": f"Cluster '{cluster}' scaffolded successfully\n",
            "files": sorted(paths.values()),
            **run,
        },
    }


TYPE_FIELDS: dict[str, dict[str, Any]] = {
    "deprovision": {
        "proposal_type": "deprovision",
        "base_commit": BASE_COMMIT,
        "trigger_effect": ["deprovision", "archive-cluster"],
        "reversibility": "reversible_before_merge",
        "commit_message": f"chore: deprovision {CLUSTER}",
        "patch": added_file(f"deprovision/{CLUSTER}-archive"),
    },
    "scaffold": {
        "proposal_type": "scaffold",
        "base_commit": BASE_COMMIT,
        "scaffold_commit": "2f6c7b4" + "0" * 33,
        "trigger_effect": ["pre-provision", "provision", "post-provision"],
        "reversibility": "reversible_before_merge",
        **scaffold_fields(),
    },
    "cluster_edit": {
        "proposal_type": "cluster_edit",
        "base_commit": BASE_COMMIT,
        "trigger_effect": [],
        "reversibility": "reversible",
        "commit_message": f"fix: backfill metadata for {CLUSTER} [skip-provision]",
        "patch": added_file(f"provision/{CLUSTER}/metadata.yaml", FULL_METADATA),
    },
    "post_provision_rerun": {
        "proposal_type": "post_provision_rerun",
        "base_commit": BASE_COMMIT,
        "trigger_effect": ["post-provision"],
        "reversibility": "irreversible",
        "pipeline_run": rerun_manifest(tier="base"),
    },
}


def make_proposal(proposal_type: str = "deprovision", **overrides: Any) -> Proposal:
    return Proposal.model_validate(
        {**BASE_FIELDS, **TYPE_FIELDS[proposal_type], **overrides}
    )


@pytest.mark.parametrize("proposal_type", sorted(TYPE_FIELDS))
def test_proposal_accepts_each_type_and_round_trips(proposal_type: str) -> None:
    proposal = make_proposal(proposal_type)

    assert proposal.status == "proposal_ready"
    assert len(proposal.proposal_digest) == 64
    assert Proposal.model_validate_json(proposal.model_dump_json()) == proposal


@pytest.mark.parametrize("newline_at_end", [True, False])
def test_proposal_accepts_cluster_edit_that_only_appends_keys(
    newline_at_end: bool,
) -> None:
    proposal = make_proposal(
        "cluster_edit",
        commit_message=f"fix: set platform for {CLUSTER}",
        patch=appended_file(
            f"provision/{CLUSTER}/metadata.yaml",
            "htpasswd-provider-name: Red Hat",
            "platform: aws",
            "aws-credentials-mode: legacy",
            "openshift-ai-version: v3",
            newline_at_end=newline_at_end,
        ),
    )

    assert "[skip-provision]" not in proposal.commit_message


def test_proposal_accepts_cluster_edit_without_openshift_ai_version() -> None:
    proposal = make_proposal(
        "cluster_edit",
        commit_message=f"fix: set platform for {CLUSTER}",
        patch=appended_file(
            f"provision/{CLUSTER}/metadata.yaml",
            "htpasswd-provider-name: Red Hat",
            "platform: aws",
            "aws-credentials-mode: legacy",
        ),
    )

    assert "openshift-ai-version" not in (proposal.patch or "")


@pytest.mark.parametrize(
    "secret",
    [
        {"secretName": "keys"},
        {"secretName": "keys", "items": [{"key": "a", "path": "a"}]},
    ],
)
def test_proposal_accepts_rerun_with_secret_workspace_by_name(
    secret: dict[str, Any],
) -> None:
    proposal = make_proposal(
        "post_provision_rerun",
        pipeline_run=rerun_with(
            metadata={"annotations": AURA_ANNOTATIONS},
            spec={
                "workspaces": [{"name": "keys", "secret": secret}],
                "taskRunTemplate": {
                    "serviceAccountName": "fleet-pipeline",
                    "podTemplate": {
                        "imagePullSecrets": [
                            {"name": "fleet-pipeline-pull-secret"}
                        ]
                    },
                },
            },
        ),
    )

    assert proposal.pipeline_run is not None


def test_proposal_rejects_changed_content_under_old_digest() -> None:
    values = make_proposal().model_dump(mode="json")
    values["summary"] = "Changed after review."

    with pytest.raises(ValidationError, match="proposal digest does not match"):
        Proposal.model_validate(values)


@pytest.mark.parametrize(
    ("proposal_type", "overrides", "message"),
    [
        (
            "deprovision",
            {"patch": added_file(f"deprovision/{CLUSTER}")},
            f"must add only deprovision/{CLUSTER}-archive",
        ),
        (
            "deprovision",
            {
                "patch": added_file(f"deprovision/{CLUSTER}-archive")
                + added_file("deprovision/other-archive")
            },
            f"must add only deprovision/{CLUSTER}-archive",
        ),
        (
            "deprovision",
            {
                "patch": added_file(
                    f"deprovision/{CLUSTER}-archive", "unexpected"
                )
            },
            f"must add deprovision/{CLUSTER}-archive as an empty regular file",
        ),
        (
            "deprovision",
            {
                "patch": added_file(f"deprovision/{CLUSTER}-archive").replace(
                    "100644", "120000"
                )
            },
            f"must add deprovision/{CLUSTER}-archive as an empty regular file",
        ),
        (
            "deprovision",
            {"commit_message": f"chore: deprovision {CLUSTER} [skip-deprovision]"},
            "must not contain [skip-deprovision]",
        ),
        (
            "scaffold",
            {"patch": added_file("provision/other/metadata.yaml", "platform: aws")},
            f"outside provision/{CLUSTER}/",
        ),
        (
            "scaffold",
            {"patch": modified_file(f"provision/{CLUSTER}/metadata.yaml")},
            "scaffold proposal may only add files",
        ),
        (
            "scaffold",
            {"commit_message": f"feat: provision {CLUSTER} [skip-provision]"},
            "must not contain [skip-provision]",
        ),
        ("scaffold", {"scaffold_commit": None}, "scaffold_commit is required"),
        *(
            (
                proposal_type,
                {"scaffold_commit": "2f6c7b4" + "0" * 33},
                "scaffold_commit is required for scaffold proposals only",
            )
            for proposal_type in (
                "deprovision",
                "cluster_edit",
                "post_provision_rerun",
            )
        ),
        (
            "cluster_edit",
            {"commit_message": f"fix: backfill metadata for {CLUSTER}"},
            "must contain [skip-provision]",
        ),
        (
            "cluster_edit",
            {"patch": deleted_file(f"provision/{CLUSTER}/metadata.yaml")},
            "cluster_edit proposal may not delete",
        ),
        (
            "cluster_edit",
            {"patch": modified_file(f"provision/{CLUSTER}/metadata.yaml")},
            "cluster_edit proposal may not change existing lines",
        ),
        (
            "cluster_edit",
            {
                "patch": hunk_patch(
                    METADATA_PATH,
                    (
                        "@@ -1,4 +1,5 @@",
                        *(f" {line}" for line in FULL_METADATA.splitlines()),
                        "+aws-credentials-mode: scoped",
                    ),
                )
            },
            "cluster_edit proposal may only add keys metadata.yaml lacks: "
            "['aws-credentials-mode']",
        ),
        (
            "cluster_edit",
            {
                "patch": hunk_patch(
                    METADATA_PATH,
                    (
                        "@@ -1,1 +1,4 @@",
                        *MISSING_KEYS,
                        " htpasswd-provider-name: Red Hat",
                    ),
                )
            },
            "cluster_edit proposal may only append to the end of",
        ),
        (
            "cluster_edit",
            {
                "patch": hunk_patch(
                    METADATA_PATH,
                    (
                        "@@ -2,1 +2,4 @@",
                        " htpasswd-provider-name: Red Hat",
                        *MISSING_KEYS,
                    ),
                )
            },
            "must change metadata.yaml in one hunk that starts at line 1",
        ),
        (
            "cluster_edit",
            {
                "patch": hunk_patch(
                    METADATA_PATH,
                    ("@@ -1,1 +1,2 @@", " a: b", "+platform: aws"),
                    ("@@ -5,1 +6,2 @@", " c: d", "+openshift-ai-version: v3"),
                )
            },
            "must change metadata.yaml in one hunk that starts at line 1",
        ),
        (
            "cluster_edit",
            {
                "patch": hunk_patch(
                    METADATA_PATH,
                    (
                        "@@ -1,1 +1,5 @@",
                        " htpasswd-provider-name: Red Hat",
                        *MISSING_KEYS,
                        "+extra: x",
                    ),
                )
            },
            "cluster_edit proposal may only add the metadata keys fleet "
            "reads: ['extra']",
        ),
        (
            "cluster_edit",
            {
                "patch": hunk_patch(
                    METADATA_PATH,
                    (
                        "@@ -1,1 +1,2 @@",
                        " htpasswd-provider-name: Red Hat",
                        "+platform: aws",
                    ),
                )
            },
            "metadata.yaml must declare platform, aws-credentials-mode, "
            "htpasswd-provider-name",
        ),
        (
            "cluster_edit",
            {"patch": added_file(METADATA_PATH, "platform: aws")},
            "metadata.yaml must declare platform, aws-credentials-mode, "
            "htpasswd-provider-name",
        ),
        (
            "cluster_edit",
            {
                "patch": hunk_patch(
                    METADATA_PATH,
                    (
                        "@@ -1,1 +1,4 @@",
                        " {htpasswd-provider-name: x}",
                        *MISSING_KEYS,
                    ),
                )
            },
            "metadata.yaml in a cluster_edit patch must be a YAML mapping",
        ),
        (
            "cluster_edit",
            {
                "patch": appended_file(
                    f"provision/{CLUSTER}/hive/kustomization.yaml",
                    "resources:",
                    "- extra",
                )
            },
            f"cluster_edit proposal may change only "
            f"provision/{CLUSTER}/metadata.yaml",
        ),
        ("cluster_edit", {"patch": ""}, "patch must contain at least one file"),
        ("deprovision", {"commit_message": "chore: deprovision"}, "must name cluster"),
        ("deprovision", {"trigger_effect": []}, "trigger_effect must be"),
        ("deprovision", {"reversibility": "reversible"}, "reversibility must be"),
        ("deprovision", {"base_commit": None}, "base_commit is required"),
        (
            "deprovision",
            {"pipeline_run": rerun_manifest()},
            "must not include pipeline_run",
        ),
        (
            "post_provision_rerun",
            {"patch": added_file("provision/x/metadata.yaml")},
            "must not include patch",
        ),
        (
            "post_provision_rerun",
            {"base_commit": None},
            "base_commit is required for post_provision_rerun proposals",
        ),
        (
            "post_provision_rerun",
            {
                "pipeline_run": {
                    **rerun_manifest(),
                    "spec": {"pipelineRef": {"name": "provision"}},
                }
            },
            "must reference the post-provision Pipeline",
        ),
        (
            "post_provision_rerun",
            {
                "pipeline_run": {
                    **rerun_manifest(),
                    "spec": {
                        "pipelineRef": {"name": "post-provision"},
                        "params": [{"name": "cluster-name", "value": "other"}],
                    },
                }
            },
            f"cluster-name parameter must be {CLUSTER}",
        ),
        (
            "post_provision_rerun",
            {"pipeline_run": rerun_manifest(**{"github-token": "value"})},
            "must not contain secret material",
        ),
        (
            "post_provision_rerun",
            {
                "pipeline_run": rerun_with(
                    spec={
                        "workspaces": [
                            {
                                "name": "keys",
                                "secret": {
                                    "secretName": "keys",
                                    "defaultMode": 256,
                                },
                            }
                        ]
                    }
                )
            },
            "workspace secret bindings may hold only secretName and items",
        ),
        (
            "post_provision_rerun",
            {
                "pipeline_run": rerun_with(
                    metadata={"annotations": {"token": "abc"}},
                    spec={"workspaces": [SECRET_WORKSPACE]},
                )
            },
            "pipeline_run annotations may contain only "
            "aura.openshiftpartnerlabs.com/proposal-id, "
            "aura.openshiftpartnerlabs.com/source-pipeline-run",
        ),
        (
            "post_provision_rerun",
            {"pipeline_run": rerun_pod(nodeSelector={"token": "abc"})},
            "must not contain secret material",
        ),
        (
            "post_provision_rerun",
            {
                "pipeline_run": rerun_with(
                    spec={
                        "taskRunTemplate": {"computeResources": {"x": "1"}}
                    }
                )
            },
            "pipeline_run taskRunTemplate may contain only "
            "serviceAccountName, podTemplate",
        ),
        (
            "post_provision_rerun",
            {"pipeline_run": rerun_pod(env=[{"name": "A", "value": "b"}])},
            "pipeline_run podTemplate may contain only securityContext, "
            "imagePullSecrets, nodeSelector, tolerations",
        ),
        (
            "post_provision_rerun",
            {
                "pipeline_run": rerun_with(
                    spec={
                        "pipelineRef": {
                            "name": "post-provision",
                            "bundle": "x",
                        }
                    }
                )
            },
            "pipeline_run pipelineRef may contain only name",
        ),
        (
            "post_provision_rerun",
            {"pipeline_run": rerun_workspace(csi={"driver": "x"})},
            "pipeline_run workspace w needs exactly one supported source",
        ),
        (
            "post_provision_rerun",
            {
                "pipeline_run": rerun_workspace(
                    emptyDir={}, readOnly=True
                )
            },
            "pipeline_run workspace w may contain only name, subPath, and "
            "its source",
        ),
        (
            "post_provision_rerun",
            {
                "pipeline_run": rerun_workspace(
                    persistentVolumeClaim={
                        "claimName": "c",
                        "readOnly": True,
                    }
                )
            },
            "pipeline_run workspace w persistentVolumeClaim may contain "
            "only claimName",
        ),
        (
            "post_provision_rerun",
            {
                "pipeline_run": rerun_workspace(
                    emptyDir={}, subPath="../other"
                )
            },
            "pipeline_run workspace w subPath must be a relative path "
            "inside the workspace",
        ),
        (
            "post_provision_rerun",
            {"pipeline_run": rerun_workspace(secret={"secretName": {"a": 1}})},
            "workspace secret bindings need a non-empty secretName",
        ),
        (
            "post_provision_rerun",
            {
                "pipeline_run": rerun_workspace(
                    secret={
                        "secretName": "k",
                        "items": [
                            {"key": "a", "path": "a", "password": "hunter2"}
                        ],
                    }
                )
            },
            "workspace secret items may hold only key, path, and mode",
        ),
        (
            "post_provision_rerun",
            {"pipeline_run": {**rerun_manifest(), "status": {}}},
            "pipeline_run may contain only apiVersion, kind, metadata, spec",
        ),
        (
            "post_provision_rerun",
            {"pipeline_run": rerun_with(metadata={"labels": {"a": "b"}})},
            "pipeline_run metadata may contain only generateName, namespace, "
            "annotations",
        ),
        (
            "post_provision_rerun",
            {"pipeline_run": rerun_with(spec={"taskRunSpecs": []})},
            "pipeline_run spec may contain only pipelineRef, params, "
            "workspaces, taskRunTemplate",
        ),
        (
            "post_provision_rerun",
            {
                "pipeline_run": rerun_with(
                    spec={
                        "params": [
                            {"name": "cluster-name", "value": CLUSTER},
                            {"name": "tier", "value": "base"},
                            {"name": "tier", "value": "premium"},
                        ]
                    }
                )
            },
            "pipeline_run parameter names must be unique: ['tier']",
        ),
        (
            "post_provision_rerun",
            {
                "pipeline_run": rerun_with(
                    metadata={"generateName": "post-provision-other-"}
                )
            },
            f"pipeline_run generateName must be post-provision-{CLUSTER}-",
        ),
        ("deprovision", {"risks": []}, "risks"),
        ("deprovision", {"risks": [" "]}, "risks"),
    ],
)
def test_proposal_rejects_invalid_contract(
    proposal_type: str,
    overrides: dict[str, Any],
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=re.escape(message)):
        make_proposal(proposal_type, **overrides)


SCAFFOLD_PATHS = sorted(f"provision/{CLUSTER}/{name}" for name in SCAFFOLD_FILES)
ALLOWED_FLAGS = (
    "--topology",
    "compact",
    "--environment",
    "production",
    "--base-domain",
    "example.com",
    "--control-plane-type",
    "m7i.8xlarge",
    "--worker-type",
    "c5.metal",
    "--control-plane-replicas",
    "3",
    "--worker-replicas",
    "0",
    "--image-set",
    "img4.21.24-x86-64-appsub",
    "--zones",
    "us-east-1a,us-east-1b",
    "--install-attempts-limit",
    "2",
    "--htpasswd-provider-name",
    "Acme Corp",
)


def test_proposal_accepts_scaffold_with_every_allowed_flag() -> None:
    arguments = scaffold_arguments(CLUSTER, *ALLOWED_FLAGS)

    proposal = make_proposal(
        "scaffold", **scaffold_fields(arguments=arguments)
    )

    assert proposal.scaffold_run is not None
    assert proposal.scaffold_run.arguments == arguments
    assert Proposal.model_validate_json(proposal.model_dump_json()) == proposal


def _arguments(*extra: str, drop: str | None = None) -> list[str]:
    arguments = scaffold_arguments(CLUSTER, *extra)
    if drop is not None:
        index = arguments.index(drop)
        del arguments[index : index + 2]
    return arguments


def _without(name: str) -> dict[str, str]:
    return {key: value for key, value in SCAFFOLD_FILES.items() if key != name}


LONG_NAME = "a" * 50


@pytest.mark.parametrize(
    ("proposal_type", "overrides", "message"),
    [
        (
            "scaffold",
            {"scaffold_run": None},
            "scaffold_run is required for scaffold proposals only",
        ),
        (
            "deprovision",
            {"scaffold_run": scaffold_fields()["scaffold_run"]},
            "scaffold_run is required for scaffold proposals only",
        ),
        (
            "scaffold",
            scaffold_fields(exit_code=1),
            "scaffold_run exit_code must be 0",
        ),
        (
            "scaffold",
            scaffold_fields(files=SCAFFOLD_PATHS[:-1]),
            "scaffold_run files must list exactly the files the patch adds, "
            "sorted",
        ),
        (
            "scaffold",
            scaffold_fields(files=[*SCAFFOLD_PATHS, f"provision/{CLUSTER}/x"]),
            "scaffold_run files must list exactly the files the patch adds, "
            "sorted",
        ),
        (
            "scaffold",
            scaffold_fields(files=SCAFFOLD_PATHS[::-1]),
            "scaffold_run files must list exactly the files the patch adds, "
            "sorted",
        ),
        (
            "scaffold",
            scaffold_fields(arguments=["gcp", *_arguments()[1:]]),
            "scaffold_run arguments must start with aws",
        ),
        (
            "scaffold",
            scaffold_fields(arguments=scaffold_arguments("other")),
            f"scaffold_run arguments must pass --name {CLUSTER}",
        ),
        (
            "scaffold",
            scaffold_fields(
                arguments=[
                    *_arguments(drop="--base-path"),
                    "--base-path",
                    "elsewhere",
                ]
            ),
            "scaffold_run arguments must pass --base-path provision",
        ),
        (
            "scaffold",
            scaffold_fields(arguments=_arguments(drop="--cluster-templates-dir")),
            "scaffold_run arguments must pass --cluster-templates-dir "
            "cluster-templates",
        ),
        *(
            (
                "scaffold",
                scaffold_fields(arguments=[*_arguments(), flag]),
                f"scaffold_run arguments must not pass {flag}",
            )
            for flag in (
                "--force",
                "--skip-validation",
                "--legacy-mint-credentials",
            )
        ),
        (
            "scaffold",
            scaffold_fields(
                arguments=_arguments("--base-domain", "a", "--base-domain", "b")
            ),
            "scaffold_run arguments repeat --base-domain",
        ),
        (
            "scaffold",
            scaffold_fields(arguments=_arguments("--foo", "x")),
            "scaffold_run arguments pass unsupported flag --foo",
        ),
        (
            "scaffold",
            scaffold_fields(arguments=_arguments("--zones=a,b")),
            "scaffold_run arguments pass unsupported flag --zones=a,b",
        ),
        (
            "scaffold",
            scaffold_fields(arguments=[*_arguments(), "--zones"]),
            "scaffold_run arguments give --zones no value",
        ),
        (
            "scaffold",
            scaffold_fields(arguments=_arguments("--topology", "--image-set", "x")),
            "scaffold_run arguments give --topology no value",
        ),
        (
            "scaffold",
            scaffold_fields(arguments=_arguments("stray")),
            "scaffold_run arguments pass stray value stray",
        ),
        (
            "scaffold",
            scaffold_fields(arguments=_arguments("--environment", "")),
            "scaffold_run arguments give --environment no value",
        ),
        (
            "scaffold",
            {
                **scaffold_fields(),
                "patch": scaffold_fields()["patch"].replace(
                    "new file mode 100644\n",
                    "new file mode 100644\nnew mode 100755\n",
                    1,
                ),
            },
            "scaffold proposal may only add regular files with mode 100644",
        ),
        (
            "scaffold",
            {
                **scaffold_fields(),
                "patch": scaffold_fields()["patch"].replace(
                    "new file mode 100644\n",
                    "new file mode 100644\nnew file mode 120000\n",
                    1,
                ),
            },
            "scaffold proposal may only add regular files with mode 100644",
        ),
        (
            "scaffold",
            {
                **scaffold_fields(),
                "patch": scaffold_fields()["patch"]
                + added_file(
                    f"provision/{CLUSTER}/metadata.yaml",
                    SCAFFOLD_FILES["metadata.yaml"],
                ),
            },
            f"scaffold proposal adds provision/{CLUSTER}/metadata.yaml more "
            "than once",
        ),
        *(
            (
                "scaffold",
                scaffold_fields(modes={"kustomization.yaml": mode}),
                "scaffold proposal may only add regular files with mode 100644",
            )
            for mode in ("120000", "100755")
        ),
        *(
            (
                "scaffold",
                scaffold_fields(contents=_without(name)),
                f"scaffold proposal must add provision/{CLUSTER}/{name}",
            )
            for name in (
                "crossplane/kustomization.yaml",
                "hive/patches/clusterdeployment.yaml",
                "hive/patches/managedcluster.yaml",
                "metadata.yaml",
            )
        ),
        *(
            (
                "scaffold",
                scaffold_fields(
                    contents={**SCAFFOLD_FILES, "metadata.yaml": metadata}
                ),
                "scaffold metadata.yaml must declare platform aws and "
                "aws-credentials-mode scoped",
            )
            for metadata in (
                "platform: aws\naws-credentials-mode: legacy",
                "platform: azure\naws-credentials-mode: scoped",
                "platform: aws",
                "- aws",
                "platform: [",
            )
        ),
        (
            "scaffold",
            scaffold_fields(LONG_NAME),
            "scaffold cluster_name must be at most 49 characters",
        ),
    ],
)
def test_proposal_rejects_invalid_scaffold(
    proposal_type: str,
    overrides: dict[str, Any],
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=re.escape(message)):
        make_proposal(proposal_type, **overrides)


def test_patch_changes_classifies_files() -> None:
    patch = (
        added_file("provision/x/a.yaml")
        + modified_file("provision/x/b.yaml")
        + deleted_file("provision/x/c.yaml")
    )

    assert patch_changes(patch) == [
        ("provision/x/a.yaml", "added"),
        ("provision/x/b.yaml", "modified"),
        ("provision/x/c.yaml", "deleted"),
    ]


@pytest.mark.parametrize(
    ("proposal_type", "trigger_effect"),
    [
        ("scaffold", []),
        ("scaffold", ["pre-provision", "provision"]),
        ("scaffold", ["provision", "pre-provision", "post-provision"]),
        ("cluster_edit", ["post-provision"]),
        ("cluster_edit", ["pre-provision", "provision", "post-provision"]),
        ("post_provision_rerun", []),
        ("post_provision_rerun", ["post-provision", "post-provision"]),
        ("post_provision_rerun", ["pre-provision"]),
    ],
)
def test_proposal_rejects_another_trigger_effect(
    proposal_type: str,
    trigger_effect: list[str],
) -> None:
    expected = [
        str(effect)
        for effect in EXPECTED_TRIGGER_EFFECTS[ProposalType(proposal_type)]
    ]

    with pytest.raises(ValidationError) as error:
        make_proposal(proposal_type, trigger_effect=trigger_effect)

    assert (
        f"trigger_effect must be {expected} for {proposal_type} proposals"
        in str(error.value)
    )


@pytest.mark.parametrize(
    "existing",
    ["pull-secret: abc", "password: hunter2", "apiKey: abc", "token: abc"],
)
def test_proposal_rejects_cluster_edit_leaving_a_secret_named_key(
    existing: str,
) -> None:
    with pytest.raises(
        ValidationError,
        match="metadata.yaml must not contain secret-named keys",
    ):
        make_proposal(
            "cluster_edit",
            commit_message=f"fix: set platform for {CLUSTER}",
            patch=appended_file(
                f"provision/{CLUSTER}/metadata.yaml",
                existing,
                "htpasswd-provider-name: Red Hat",
                "platform: aws",
                "aws-credentials-mode: legacy",
                "openshift-ai-version: v3",
            ),
        )


def test_proposal_keeps_htpasswd_provider_name_in_cluster_edit() -> None:
    proposal = make_proposal(
        "cluster_edit",
        commit_message=f"fix: set platform for {CLUSTER}",
        patch=appended_file(
            f"provision/{CLUSTER}/metadata.yaml",
            "htpasswd-provider-name: Red Hat",
            "platform: aws",
            "aws-credentials-mode: legacy",
            "openshift-ai-version: v3",
        ),
    )

    assert "htpasswd-provider-name: Red Hat" in (proposal.patch or "")


def _metadata_hunk(header: str, *lines: str) -> str:
    path = f"provision/{CLUSTER}/metadata.yaml"
    return "\n".join(
        [
            f"diff --git a/{path} b/{path}",
            "index 1111111..2222222 100644",
            f"--- a/{path}",
            f"+++ b/{path}",
            header,
            *lines,
        ]
    ) + "\n"


@pytest.mark.parametrize(
    ("patch", "message"),
    [
        (
            _metadata_hunk(
                "@@ -1,3 +1,4 @@",
                " platform: aws",
                " aws-credentials-mode: legacy",
                " htpasswd-provider-name: >-",
                "+  openshift-ai-version: v3",
            ),
            "cluster_edit append changes how metadata.yaml parses",
        ),
        (
            _metadata_hunk(
                "@@ -1,5 +1,6 @@",
                " platform: aws",
                " aws-credentials-mode: legacy",
                "+htpasswd-provider-name: x",
            ),
            "cluster_edit proposal must change metadata.yaml in one hunk "
            "that starts at line 1",
        ),
    ],
    ids=["append-changes-parsing", "hunk-length-mismatch"],
)
def test_proposal_rejects_a_cluster_edit_hunk_the_contract_cannot_trust(
    patch: str,
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=re.escape(message)):
        make_proposal(
            "cluster_edit",
            commit_message=f"fix: set keys for {CLUSTER}",
            patch=patch,
        )


@pytest.mark.parametrize(
    "metadata",
    [
        {"namespace": "default"},
        {"generateName": f"post-provision-{CLUSTER}-"},
    ],
    ids=["other-namespace", "no-namespace"],
)
def test_proposal_rejects_a_rerun_outside_the_pipelines_namespace(
    metadata: dict[str, str],
) -> None:
    manifest = rerun_manifest(tier="base")
    manifest["metadata"] = (
        {**manifest["metadata"], **metadata}
        if "namespace" in metadata
        else metadata
    )

    with pytest.raises(
        ValidationError,
        match="pipeline_run must use generateName in openshift-pipelines",
    ):
        make_proposal("post_provision_rerun", pipeline_run=manifest)
