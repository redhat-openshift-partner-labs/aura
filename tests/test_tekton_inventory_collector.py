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
        },
        {
            "name": "pull-secret",
            "source": "secret",
            "reference": "pull-secret",
            "sub_path": None,
            "claim_spec": None,
        },
        {
            "name": "config",
            "source": "configMap",
            "reference": "fleet-config",
            "sub_path": "settings",
            "claim_spec": None,
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
