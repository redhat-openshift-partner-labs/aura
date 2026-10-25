from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from aura.collectors.cluster_state import ClusterStateCollector
from aura.collectors.git_inventory import GitInventoryCollector
from aura.collectors.pipeline_definitions import PipelineDefinitionCollector
from aura.collectors.rerun_evidence import RerunEvidenceCollector
from aura.collectors.tekton_inventory import TektonInventoryCollector
from aura.contracts import EvidenceSnapshot
from aura.proposals import Proposal, ProposalType, Reversibility, TriggerEffect
from aura.services.proposal import ProposalRefused
from aura.services.rerun import PostProvisionRerunGenerator
from test_cluster_edit_proposal import ABSENT, fleet
from test_cluster_inventory_collector import actual_state, openshift_collector
from test_cluster_state_collector import EmptyCollector
from test_cluster_state_diagnosis import assert_references, make_cwa_snapshot
from test_git_inventory_collector import write_cluster
from test_pipeline_definitions import (
    POST_PROVISION,
    FakePipelineClient,
    param,
    pipeline,
)


TRACE_ID = UUID(int=70)
CREATED_AT = datetime(2026, 1, 1, tzinfo=UTC)
FULL_METADATA = (
    "platform: aws\naws-credentials-mode: legacy\n"
    "htpasswd-provider-name: htpasswd\nopenshift-ai-version: v3\n"
)
TEMPLATE = {
    "serviceAccountName": "fleet-pipeline",
    "podTemplate": {
        "securityContext": {"fsGroup": 0},
        "imagePullSecrets": [{"name": "fleet-pipeline-pull-secret"}],
    },
}
SHARED = {
    "name": "shared-workspace",
    "volumeClaimTemplate": {
        "spec": {
            "accessModes": ["ReadWriteOnce"],
            "resources": {"requests": {"storage": "1Gi"}},
            "storageClassName": "gp3-csi",
        }
    },
}
LISTENER_PARAMS = {
    "cluster-name": "example",
    "tier": "base",
    "dns-zones": "*.apps.example.example.com,api.example.example.com",
}
TRIGGER_PARAMS = {
    **LISTENER_PARAMS,
    "htpasswd-provider-name": "htpasswd",
    "openshift-ai-version": "v3",
}

AI_PARAMS = {**TRIGGER_PARAMS, "tier": "ai"}


def run(
    name: str,
    start: str,
    *,
    params: dict[str, Any] | None = None,
    succeeded: str | None = "True",
    pipeline_name: str = "post-provision",
    namespace: str = "openshift-pipelines",
    **spec: Any,
) -> dict[str, Any]:
    return {
        "metadata": {
            "name": name,
            "namespace": namespace,
            "uid": f"uid-{name}",
        },
        "spec": {
            "pipelineRef": {"name": pipeline_name},
            "params": [
                {"name": key, "value": value}
                for key, value in (params or TRIGGER_PARAMS).items()
            ],
            "taskRunTemplate": TEMPLATE,
            "workspaces": [SHARED],
            "timeouts": {"pipeline": "1h0m0s"},
            **spec,
        },
        "status": {
            **({"startTime": start} if start else {}),
            "conditions": (
                []
                if succeeded is None
                else [{"type": "Succeeded", "status": succeeded}]
            ),
        },
    }


class RunsClient:
    def __init__(self, *runs: dict[str, Any]) -> None:
        self._runs = list(runs)

    def list_pipeline_runs(self) -> list[dict[str, Any]]:
        return self._runs

    def list_task_runs(self) -> list[dict[str, Any]]:
        return []

    def resource_version(self) -> str:
        return "pipelineruns=1;taskruns=1"


def rerun_collector(
    root: Path,
    *runs: dict[str, Any],
    openshift: object | None = None,
    definition: dict[str, Any] = POST_PROVISION,
    pipeline_run: str | None = None,
) -> RerunEvidenceCollector:
    cluster_state = ClusterStateCollector(
        git_collector=GitInventoryCollector(root),
        openshift_collector=openshift
        or openshift_collector(actual_state("example")),
        tekton_collector=TektonInventoryCollector(client=RunsClient(*runs)),
        cluster_name="example",
        pipeline_run=pipeline_run,
        pipeline_name="post-provision",
    )
    return RerunEvidenceCollector(
        cluster_state=cluster_state,
        pipeline_definitions=PipelineDefinitionCollector(
            client=FakePipelineClient(definition),
            pipeline_names={"post-provision"},
        ),
    )


def evidence(root: Path, *runs: dict[str, Any], **options: Any):
    return rerun_collector(root, *runs, **options).collect(
        command_id=UUID(int=1),
        snapshot_id=UUID(int=40),
        collected_at=CREATED_AT,
    )


def generate(snapshot: EvidenceSnapshot) -> Proposal:
    return PostProvisionRerunGenerator().generate(
        make_cwa_snapshot(snapshot),
        cwa_trace_id=TRACE_ID,
        created_at=CREATED_AT,
    )


LATEST = run("post-provision-example-new", "2026-01-02T00:00:00Z")
OLDER = run(
    "post-provision-example-old",
    "2026-01-01T00:00:00Z",
    params={**TRIGGER_PARAMS, "tier": "premium"},
)


def test_evidence_combines_cluster_state_and_pipeline_definition(
    tmp_path: Path,
) -> None:
    snapshot = evidence(fleet(tmp_path, FULL_METADATA), LATEST)

    assert snapshot.collector == "post_provision_rerun"
    assert snapshot.source == "git+openshift+tekton"
    assert snapshot.source_version.endswith(";pipelines=55")
    assert {item.kind for item in snapshot.items} >= {
        "cluster_definition",
        "cluster_actual_state",
        "pipeline_run",
        "cluster_state_correlation",
        "pipeline_definition",
    }


def test_generator_copies_the_source_run(tmp_path: Path) -> None:
    snapshot = evidence(fleet(tmp_path, FULL_METADATA), OLDER, LATEST)

    proposal = generate(snapshot)

    assert_references(proposal, snapshot, TRACE_ID)
    assert proposal.proposal_type is ProposalType.POST_PROVISION_RERUN
    assert proposal.trigger_effect == [TriggerEffect.POST_PROVISION]
    assert proposal.reversibility is Reversibility.IRREVERSIBLE
    assert proposal.base_commit is None
    assert proposal.pipeline_run == {
        "apiVersion": "tekton.dev/v1",
        "kind": "PipelineRun",
        "metadata": {
            "generateName": "post-provision-example-",
            "namespace": "openshift-pipelines",
            "annotations": {
                "aura.openshiftpartnerlabs.com/proposal-id": str(
                    proposal.proposal_id
                ),
                "aura.openshiftpartnerlabs.com/source-pipeline-run": (
                    "post-provision-example-new"
                ),
            },
        },
        "spec": {
            "pipelineRef": {"name": "post-provision"},
            "params": [
                {"name": key, "value": value}
                for key, value in TRIGGER_PARAMS.items()
            ],
            "workspaces": [SHARED],
            "taskRunTemplate": TEMPLATE,
        },
    }
    assert "post-provision-example-new" in proposal.summary
    assert any("dns-zones" in risk for risk in proposal.risks)
    assert generate(snapshot) == proposal
    assert Proposal.model_validate_json(proposal.model_dump_json()) == proposal


def test_generator_honors_a_named_source_run(tmp_path: Path) -> None:
    snapshot = evidence(
        fleet(tmp_path, FULL_METADATA),
        OLDER,
        LATEST,
        pipeline_run="post-provision-example-old",
    )

    params = generate(snapshot).pipeline_run["spec"]["params"]

    assert {"name": "tier", "value": "premium"} in params


def test_generator_accepts_listener_runs_matching_metadata_defaults(
    tmp_path: Path,
) -> None:
    listener = run(
        "post-provision-example-el",
        "2026-01-02T00:00:00Z",
        params=LISTENER_PARAMS,
    )

    manifest = generate(evidence(fleet(tmp_path), listener)).pipeline_run

    assert [p["name"] for p in manifest["spec"]["params"]] == list(
        LISTENER_PARAMS
    )


def test_generator_ignores_openshift_ai_version_off_ai_tier(
    tmp_path: Path,
) -> None:
    source = run(
        "post-provision-example-new",
        "2026-01-02T00:00:00Z",
        params={**TRIGGER_PARAMS, "openshift-ai-version": "v2"},
    )

    proposal = generate(evidence(fleet(tmp_path, FULL_METADATA), source))

    assert {"name": "openshift-ai-version", "value": "v2"} in (
        proposal.pipeline_run["spec"]["params"]
    )
    assert any(
        "htpasswd-provider-name was checked" in risk for risk in proposal.risks
    )
    assert not any("openshift-ai-version" in risk for risk in proposal.risks)


def test_generator_checks_openshift_ai_version_on_ai_tier(
    tmp_path: Path,
) -> None:
    source = run(
        "post-provision-example-new", "2026-01-02T00:00:00Z", params=AI_PARAMS
    )

    proposal = generate(evidence(fleet(tmp_path), source))

    assert any(
        "htpasswd-provider-name and openshift-ai-version were checked" in risk
        for risk in proposal.risks
    )


def test_generator_keeps_secret_workspaces_by_name(tmp_path: Path) -> None:
    keys = {"name": "keys", "secret": {"secretName": "spoke-keys"}}
    source = run(
        "post-provision-example-new",
        "2026-01-02T00:00:00Z",
        workspaces=[SHARED, keys],
    )

    manifest = generate(
        evidence(fleet(tmp_path, FULL_METADATA), source)
    ).pipeline_run

    assert manifest["spec"]["workspaces"] == [SHARED, keys]


def _not_provisioned(tmp_path: Path) -> EvidenceSnapshot:
    state = actual_state("example")
    state = state.model_copy(
        update={
            "data": {
                **state.data,
                "cluster_deployment_conditions": [
                    {"type": "Provisioned", "status": "False"}
                ],
            }
        }
    )
    return evidence(
        fleet(tmp_path, FULL_METADATA),
        LATEST,
        openshift=openshift_collector(state),
    )


def _with_sentinel(root: Path) -> None:
    (root / "deprovision").mkdir(exist_ok=True)
    (root / "deprovision" / "example-archive").write_text("")


def _drop_managed_cluster(root: Path) -> None:
    (root / "provision/example/hive/patches/managedcluster.yaml").unlink()


def _case(
    *runs: dict[str, Any],
    metadata: object = FULL_METADATA,
    prepare: Callable[[Path], None] | None = None,
    **options: Any,
) -> Callable[[Path], EvidenceSnapshot]:
    def build(tmp_path: Path) -> EvidenceSnapshot:
        return evidence(
            fleet(tmp_path, metadata, prepare=prepare), *runs, **options
        )

    return build


NEW = "PipelineRun post-provision-example-new"


@pytest.mark.parametrize(
    ("build", "reason"),
    [
        (_not_provisioned, "cluster example is not Provisioned"),
        (
            _case(LATEST, prepare=_with_sentinel),
            "deprovision sentinel already exists: deprovision/example-archive",
        ),
        (
            _case(LATEST, openshift=EmptyCollector()),
            "no OpenShift resources for cluster example",
        ),
        (_case(), "no post-provision PipelineRun for cluster example"),
        (
            _case(
                run(
                    "post-provision-example-new",
                    "2026-01-02T00:00:00Z",
                    succeeded=None,
                ),
                OLDER,
            ),
            "post-provision runs for cluster example have not finished: "
            "['post-provision-example-new']",
        ),
        (
            _case(
                run(
                    "provision-example",
                    "2026-01-03T00:00:00Z",
                    pipeline_name="provision",
                ),
                LATEST,
                pipeline_run="provision-example",
            ),
            "Selected Tekton PipelineRun provision-example",
        ),
        (
            _case(
                run(
                    "post-provision-example-new",
                    "2026-01-02T00:00:00Z",
                    params={"cluster-name": "example", "tier": "base"},
                )
            ),
            f"{NEW} lacks required parameters: ['dns-zones']",
        ),
        (
            _case(
                run(
                    "post-provision-example-new",
                    "2026-01-02T00:00:00Z",
                    params={**TRIGGER_PARAMS, "extra": "x"},
                )
            ),
            f"{NEW} sets parameters the post-provision Pipeline does not "
            "declare: ['extra']",
        ),
        (
            _case(
                run(
                    "post-provision-example-new",
                    "2026-01-02T00:00:00Z",
                    params={**TRIGGER_PARAMS, "github-token": "abc"},
                ),
                definition=pipeline(
                    "post-provision",
                    *POST_PROVISION["spec"]["params"],
                    param("github-token", ""),
                ),
            ),
            f"{NEW} parameter github-token is redacted",
        ),
        (
            _case(
                run(
                    "post-provision-example-new",
                    "2026-01-02T00:00:00Z",
                    params=LISTENER_PARAMS,
                ),
                metadata=(
                    "platform: aws\naws-credentials-mode: legacy\n"
                    "htpasswd-provider-name: VirtCookbook\n"
                ),
            ),
            f"{NEW} would use htpasswd-provider-name htpasswd but "
            "metadata.yaml declares VirtCookbook",
        ),
        (
            _case(
                run(
                    "post-provision-example-new",
                    "2026-01-02T00:00:00Z",
                    params={**AI_PARAMS, "openshift-ai-version": "v2"},
                ),
            ),
            f"{NEW} would use openshift-ai-version v2 but metadata.yaml "
            "declares v3",
        ),
        (
            _case(
                run(
                    "post-provision-example-new",
                    "2026-01-02T00:00:00Z",
                    params={**AI_PARAMS, "openshift-ai-version": "v2"},
                ),
                metadata=ABSENT,
            ),
            f"{NEW} would use openshift-ai-version v2 but metadata.yaml "
            "defaults to v3",
        ),
        (
            _case(
                run(
                    "post-provision-example-new",
                    "2026-01-02T00:00:00Z",
                    taskRunTemplate={
                        **TEMPLATE,
                        "podTemplate": {"env": [{"name": "A", "value": "b"}]},
                    },
                )
            ),
            f"{NEW} has fields a rerun cannot copy: "
            "['spec.taskRunTemplate.podTemplate.env']",
        ),
        (
            _case(
                run(
                    "post-provision-example-new",
                    "2026-01-02T00:00:00Z",
                    workspaces=[
                        {
                            "name": "keys",
                            "secret": {"secretName": "k", "defaultMode": 256},
                        }
                    ],
                )
            ),
            f"{NEW} has fields a rerun cannot copy: "
            "['workspaces.keys.secret.defaultMode']",
        ),
        (
            _case(
                run(
                    "post-provision-example-new",
                    "2026-01-02T00:00:00Z",
                    workspaces=[{"name": "creds", "csi": {"driver": "x"}}],
                )
            ),
            f"{NEW} workspace creds uses source csi, which a rerun cannot "
            "copy",
        ),
        (
            _case(
                OLDER,
                run(
                    "post-provision-example-new",
                    "2026-01-02T00:00:00Z",
                    succeeded=None,
                ),
                pipeline_run="post-provision-example-old",
            ),
            "post-provision runs for cluster example have not finished: "
            "['post-provision-example-new']",
        ),
        (
            _case(
                LATEST,
                run("post-provision-example-pending", "", succeeded=None),
            ),
            "post-provision runs for cluster example have not finished: "
            "['post-provision-example-pending']",
        ),
        (
            _case(
                run(
                    "post-provision-example-new",
                    "2026-01-02T00:00:00Z",
                    namespace="example",
                )
            ),
            f"{NEW} is in namespace example, not openshift-pipelines",
        ),
        (
            _case(
                run(
                    "post-provision-example-new",
                    "2026-01-02T00:00:00Z",
                    namespace="example-ns",
                    params={**TRIGGER_PARAMS, "cluster-name": "other"},
                )
            ),
            f"{NEW} is for cluster other, not example",
        ),
        (
            _case(LATEST, prepare=_drop_managed_cluster),
            "cannot check metadata.yaml for example: its definition is "
            "missing ['hive/patches/managedcluster.yaml']",
        ),
        (
            _case(
                LATEST,
                definition=pipeline(
                    "post-provision",
                    *POST_PROVISION["spec"]["params"],
                    "not-a-mapping",
                ),
            ),
            "the post-provision Pipeline has 1 malformed parameters",
        ),
    ],
)
def test_generator_refuses_unmet_preconditions(
    tmp_path: Path,
    build: Callable[[Path], EvidenceSnapshot],
    reason: str,
) -> None:
    with pytest.raises(ProposalRefused) as refusal:
        generate(build(tmp_path))

    assert any(item.startswith(reason) for item in refusal.value.reasons)


def _archive(root: Path) -> None:
    write_cluster(root, directory="archive")


def test_generator_skips_the_metadata_check_without_one_definition(
    tmp_path: Path,
) -> None:
    source = run(
        "post-provision-example-new",
        "2026-01-02T00:00:00Z",
        params={**TRIGGER_PARAMS, "htpasswd-provider-name": "Basic"},
    )

    with pytest.raises(ProposalRefused) as refusal:
        generate(evidence(fleet(tmp_path, prepare=_archive), source))

    assert refusal.value.reasons == [
        "conflicting Git cluster definitions for example"
    ]
