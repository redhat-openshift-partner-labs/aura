from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from pydantic import ValidationError

from aura.proposals import Proposal, patch_changes


BASE_COMMIT = "492bc8e07afddcd557441ddb7d72158de60ab8cb"
CLUSTER = "0171d46a-scopedaws"


def added_file(path: str, content: str = "") -> str:
    lines = [
        f"diff --git a/{path} b/{path}",
        "new file mode 100644",
        "index 0000000..e69de29",
    ]
    if content:
        lines += ["--- /dev/null", f"+++ b/{path}", "@@ -0,0 +1 @@", f"+{content}"]
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
        "scaffold_commit": "e5346c3" + "0" * 33,
        "trigger_effect": ["pre-provision", "provision", "post-provision"],
        "reversibility": "reversible_before_merge",
        "commit_message": f"feat: provision {CLUSTER}",
        "patch": added_file(f"provision/{CLUSTER}/metadata.yaml", "platform: aws")
        + added_file(f"provision/{CLUSTER}/kustomization.yaml", "resources: []"),
    },
    "cluster_edit": {
        "proposal_type": "cluster_edit",
        "base_commit": BASE_COMMIT,
        "trigger_effect": [],
        "reversibility": "reversible",
        "commit_message": f"fix: backfill metadata for {CLUSTER} [skip-provision]",
        "patch": added_file(f"provision/{CLUSTER}/metadata.yaml", "platform: aws"),
    },
    "post_provision_rerun": {
        "proposal_type": "post_provision_rerun",
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
            newline_at_end=newline_at_end,
        ),
    )

    assert "[skip-provision]" not in proposal.commit_message


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
