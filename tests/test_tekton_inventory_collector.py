from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, final
from uuid import UUID

from aura.collectors.tekton_inventory import TektonInventoryCollector


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
        "status": "Failed",
        "start_time": "2026-01-01T00:00:00Z",
        "completion_time": "2026-01-01T00:05:00Z",
        "conditions": [
            {
                "type": "Succeeded",
                "status": "False",
                "reason": "Failed",
                "message": "PipelineRun failed",
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
            "message": "provision failed",
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
