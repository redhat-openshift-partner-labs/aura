from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from aura.collectors.workflow_inventory import WorkflowInventoryCollector
from test_cluster_inventory_collector import (
    StaticSourceCollector,
    actual_state,
    definition,
    pipeline_run,
)


def test_workflow_inventory_collector_correlates_cluster() -> None:
    collector = WorkflowInventoryCollector(
        git_collector=StaticSourceCollector(
            collector="git_inventory",
            source="fleet-clusters",
            snapshot_id=UUID(int=10),
            items=[definition("example")],
        ),
        openshift_collector=StaticSourceCollector(
            collector="openshift_inventory",
            source="openshift",
            snapshot_id=UUID(int=20),
            items=[actual_state("example")],
        ),
        tekton_collector=StaticSourceCollector(
            collector="tekton_inventory",
            source="tekton",
            snapshot_id=UUID(int=30),
            items=[pipeline_run("example")],
        ),
    )

    snapshot = collector.collect(
        command_id=UUID(int=1),
        snapshot_id=UUID(int=40),
        collected_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    assert snapshot.collector == "workflow_inventory"
    assert snapshot.source == "git+openshift+tekton"
    assert snapshot.source_version == (
        "git=fleet-clusters-version;"
        "openshift=openshift-version;"
        "tekton=tekton-version"
    )
    assert len(snapshot.items) == 4
    view = snapshot.items[-1]
    assert view.kind == "workflow_view"
    assert view.data == {
        "pipeline_run_name": "deploy-cluster",
        "pipeline_name": "deploy-cluster-pipeline",
        "status": "Failed",
        "start_time": "2026-01-01T00:00:00Z",
        "completion_time": "2026-01-01T00:05:00Z",
        "failed_task": {"name": "provision-task"},
        "related_cluster": "example",
        "next_diagnostic_step": "Inspect TaskRun provision-task.",
    }


def test_workflow_inventory_collector_works_without_git() -> None:
    collector = WorkflowInventoryCollector(
        openshift_collector=StaticSourceCollector(
            collector="openshift_inventory",
            source="openshift",
            snapshot_id=UUID(int=20),
            items=[actual_state("example")],
        ),
        tekton_collector=StaticSourceCollector(
            collector="tekton_inventory",
            source="tekton",
            snapshot_id=UUID(int=30),
            items=[pipeline_run("example")],
        ),
    )

    snapshot = collector.collect(
        command_id=UUID(int=1),
        snapshot_id=UUID(int=40),
        collected_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    assert snapshot.source == "openshift+tekton"
    assert snapshot.source_version == (
        "openshift=openshift-version;tekton=tekton-version"
    )
    assert snapshot.items[-1].data["related_cluster"] == "example"
