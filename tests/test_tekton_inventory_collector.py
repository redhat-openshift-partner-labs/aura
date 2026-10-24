from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, final
from uuid import UUID

import pytest

from aura.collectors.cluster_inventory import ClusterInventoryCollector
from aura.collectors.cluster_state import ClusterStateCollector
from aura.collectors.tekton_inventory import TektonInventoryCollector
from aura.collectors.workflow_inventory import WorkflowInventoryCollector
from test_cluster_state_collector import EmptyCollector


REDACTION = "pipeline_run[example-ns/deploy-cluster-abc].params.github-token"


@final
class FakeTektonClient:
    def list_pipeline_runs(self) -> list[dict[str, Any]]:
        return [
            {
                "metadata": {
                    "name": "deploy-cluster-abc",
                    "namespace": "example-ns",
                    "uid": "0171d46a-0000-0000-0000-000000000031",
                },
                "spec": {
                    "pipelineRef": {"name": "deploy-cluster"},
                    "params": [
                        {"name": "cluster-name", "value": "example"}
                    ],
                },
                "status": {
                    "startTime": "2026-01-01T00:00:00Z",
                    "completionTime": "2026-01-01T00:05:00Z",
                    "conditions": [
                        {
                            "type": "Succeeded",
                            "status": "False",
                            "reason": "Failed",
                            "message": "PipelineRun failed",
                        }
                    ],
                },
            }
        ]

    def list_task_runs(self) -> list[dict[str, Any]]:
        return [
            {
                "metadata": {
                    "name": "provision-task-abc",
                    "namespace": "example-ns",
                    "labels": {
                        "tekton.dev/pipelineRun": "deploy-cluster-abc",
                        "tekton.dev/pipelineTask": "provision",
                    },
                },
                "status": {
                    "conditions": [
                        {
                            "type": "Succeeded",
                            "status": "False",
                            "reason": "TaskRunFailed",
                            "message": "provision failed",
                        }
                    ]
                },
            }
        ]

    def resource_version(self) -> str:
        return "pipelineruns=789;taskruns=790"


def test_tekton_collector_returns_pipeline_run_with_failed_task() -> None:
    collector = TektonInventoryCollector(client=FakeTektonClient())

    snapshot = collector.collect(
        command_id=UUID("00000000-0000-0000-0000-000000000001"),
        snapshot_id=UUID("00000000-0000-0000-0000-000000000050"),
        collected_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    assert snapshot.collector == "tekton_inventory"
    assert snapshot.source == "tekton"
    assert snapshot.source_version == "pipelineruns=789;taskruns=790"
    assert len(snapshot.items) == 1

    item = snapshot.items[0]
    assert item.kind == "pipeline_run"
    assert item.data == {
        "pipeline_run_name": "deploy-cluster-abc",
        "pipeline_run_id": "0171d46a-0000-0000-0000-000000000031",
        "namespace": "example-ns",
        "cluster_name": "example",
        "pipeline_name": "deploy-cluster",
        "params": {"cluster-name": "example"},
        "workspaces": [],
        "run_template": {
            "service_account_name": None,
            "pod_template": {},
            "unsupported_fields": [],
        },
        "status": "Failed",
        "start_time": "2026-01-01T00:00:00Z",
        "completion_time": "2026-01-01T00:05:00Z",
        "conditions": [
            {
                "type": "Succeeded",
                "status": "False",
                "reason": "Failed",
            }
        ],
        "task_runs": [
            {
                "name": "provision-task-abc",
                "pipeline_task": "provision",
                "status": "Failed",
            }
        ],
        "failed_task": {
            "name": "provision-task-abc",
            "pipeline_task": "provision",
            "status": "Failed",
            "reason": "TaskRunFailed",
        },
    }


def test_tekton_collector_uses_deterministic_item_id() -> None:
    collector = TektonInventoryCollector(client=FakeTektonClient())
    command_id = UUID("00000000-0000-0000-0000-000000000001")

    snapshot = collector.collect(
        command_id=command_id,
        snapshot_id=UUID("00000000-0000-0000-0000-000000000050"),
        collected_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    expected_item_id = UUID(
        "85d2426c-69fb-5f00-b8fa-0535f317e8ef"
    )
    assert snapshot.items[0].item_id == expected_item_id


@final
class ParameterTektonClient:
    def list_pipeline_runs(self) -> list[dict[str, Any]]:
        return [
            {
                "metadata": {
                    "name": "deploy-cluster-abc",
                    "namespace": "example-ns",
                },
                "spec": {
                    "params": [
                        {"name": "cluster-name", "value": "example"},
                        {"name": "github-token", "value": "token-value"},
                    ],
                    "workspaces": [
                        {
                            "name": "shared-workspace",
                            "volumeClaimTemplate": {
                                "spec": {"accessModes": ["ReadWriteOnce"]}
                            },
                        },
                        {
                            "name": "pull-secret",
                            "secret": {"secretName": "pull-secret"},
                        },
                        {
                            "name": "config",
                            "configMap": {"name": "fleet-config"},
                            "subPath": "settings",
                        },
                    ],
                },
            }
        ]

    def list_task_runs(self) -> list[dict[str, Any]]:
        return []

    def resource_version(self) -> str:
        return "pipelineruns=1;taskruns=1"


def test_tekton_collector_records_redacted_params_and_workspaces() -> None:
    collector = TektonInventoryCollector(client=ParameterTektonClient())

    snapshot = collector.collect(command_id=UUID(int=1))

    data = snapshot.items[0].data
    assert data["params"] == {
        "cluster-name": "example",
        "github-token": "[REDACTED]",
    }
    assert data["workspaces"] == [
        {
            "name": "shared-workspace",
            "source": "volumeClaimTemplate",
            "reference": None,
            "sub_path": None,
            "claim_spec": {"accessModes": ["ReadWriteOnce"]},
            "unsupported_fields": [],
        },
        {
            "name": "pull-secret",
            "source": "secret",
            "reference": "pull-secret",
            "sub_path": None,
            "claim_spec": None,
            "unsupported_fields": [],
        },
        {
            "name": "config",
            "source": "configMap",
            "reference": "fleet-config",
            "sub_path": "settings",
            "claim_spec": None,
            "unsupported_fields": [],
        },
    ]
    assert snapshot.redactions == [REDACTION]


@pytest.mark.parametrize(
    ("combined", "expected"),
    [
        ("cluster_inventory", [REDACTION]),
        ("workflow_inventory", [REDACTION]),
        ("cluster_state", [REDACTION]),
        ("cluster_state_other", []),
    ],
)
def test_combined_collectors_keep_included_tekton_redactions(
    combined: str,
    expected: list[str],
) -> None:
    sources = {
        "git_collector": EmptyCollector(),
        "openshift_collector": EmptyCollector(),
        "tekton_collector": TektonInventoryCollector(
            client=ParameterTektonClient()
        ),
    }
    collector = {
        "cluster_inventory": lambda: ClusterInventoryCollector(**sources),
        "workflow_inventory": lambda: WorkflowInventoryCollector(**sources),
        "cluster_state": lambda: ClusterStateCollector(
            **sources, cluster_name="example"
        ),
        "cluster_state_other": lambda: ClusterStateCollector(
            **sources, cluster_name="other"
        ),
    }[combined]()

    snapshot = collector.collect(command_id=UUID(int=1))

    assert snapshot.redactions == expected


class SpecTektonClient:
    def __init__(self, spec: dict[str, Any]) -> None:
        self._spec = spec

    def list_pipeline_runs(self) -> list[dict[str, Any]]:
        return [
            {
                "metadata": {
                    "name": "post-provision-example-abc",
                    "namespace": "openshift-pipelines",
                },
                "spec": {
                    "pipelineRef": {"name": "post-provision"},
                    **self._spec,
                },
            }
        ]

    def list_task_runs(self) -> list[dict[str, Any]]:
        return []

    def resource_version(self) -> str:
        return "pipelineruns=1;taskruns=1"


def run_data(spec: dict[str, Any]) -> dict[str, Any]:
    snapshot = TektonInventoryCollector(client=SpecTektonClient(spec)).collect(
        command_id=UUID(int=1)
    )
    return snapshot.items[0].data


FLEET_TEMPLATE = {
    "serviceAccountName": "fleet-pipeline",
    "podTemplate": {
        "securityContext": {"fsGroup": 0},
        "imagePullSecrets": [{"name": "fleet-pipeline-pull-secret"}],
    },
}


def test_tekton_collector_records_the_run_template() -> None:
    data = run_data(
        {"taskRunTemplate": FLEET_TEMPLATE, "timeouts": {"pipeline": "1h0m0s"}}
    )

    assert data["run_template"] == {
        "service_account_name": "fleet-pipeline",
        "pod_template": FLEET_TEMPLATE["podTemplate"],
        "unsupported_fields": [],
    }


def test_tekton_collector_names_unsupported_run_fields_only() -> None:
    template = {
        **FLEET_TEMPLATE,
        "computeResources": {"limits": {"cpu": "1"}},
        "podTemplate": {
            **FLEET_TEMPLATE["podTemplate"],
            "env": [{"name": "API", "value": "hunter2"}],
        },
    }
    snapshot = TektonInventoryCollector(
        client=SpecTektonClient(
            {"taskRunTemplate": template, "taskRunSpecs": [{"x": "hunter2"}]}
        )
    ).collect(command_id=UUID(int=1))

    data = snapshot.items[0].data
    assert data["run_template"]["unsupported_fields"] == [
        "spec.taskRunSpecs",
        "spec.taskRunTemplate.computeResources",
        "spec.taskRunTemplate.podTemplate.env",
    ]
    assert data["run_template"]["pod_template"] == (
        FLEET_TEMPLATE["podTemplate"]
    )
    assert "hunter2" not in snapshot.model_dump_json()


def test_tekton_collector_records_runs_without_a_run_template() -> None:
    assert run_data({})["run_template"] == {
        "service_account_name": None,
        "pod_template": {},
        "unsupported_fields": [],
    }


def test_tekton_collector_names_unsupported_workspace_fields() -> None:
    workspaces = run_data(
        {
            "workspaces": [
                {
                    "name": "keys",
                    "secret": {
                        "secretName": "keys",
                        "items": [{"key": "a", "path": "a"}],
                        "defaultMode": 256,
                    },
                },
                {"name": "scratch", "emptyDir": {"medium": "Memory"}},
                {
                    "name": "shared",
                    "volumeClaimTemplate": {
                        "metadata": {"name": "x"},
                        "spec": {"accessModes": ["ReadWriteOnce"]},
                    },
                },
            ]
        }
    )["workspaces"]

    assert [w["unsupported_fields"] for w in workspaces] == [
        ["secret.defaultMode", "secret.items"],
        ["emptyDir.medium"],
        ["volumeClaimTemplate.metadata"],
    ]
