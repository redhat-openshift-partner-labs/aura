from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import final
from uuid import UUID

from aura.cli import main
from aura.collectors.cluster_inventory import ClusterInventoryCollector
from aura.contracts import EvidenceItem, EvidenceSnapshot


@final
class StaticSourceCollector:
    def __init__(
        self,
        *,
        collector: str,
        source: str,
        snapshot_id: UUID,
        items: list[EvidenceItem],
    ) -> None:
        self._collector = collector
        self._source = source
        self._snapshot_id = snapshot_id
        self._items = items

    def collect(
        self,
        *,
        command_id: UUID,
        snapshot_id: UUID | None = None,
        collected_at: datetime | None = None,
        source_version: str | None = None,
    ) -> EvidenceSnapshot:
        return EvidenceSnapshot(
            snapshot_id=snapshot_id or self._snapshot_id,
            command_id=command_id,
            collector=self._collector,
            collector_version="test",
            collected_at=collected_at or datetime(2026, 1, 1, tzinfo=UTC),
            source=self._source,
            source_version=source_version or f"{self._source}-version",
            items=self._items,
            redactions=[],
        )


def git_collector(*items: EvidenceItem) -> StaticSourceCollector:
    return StaticSourceCollector(
        collector="git_inventory",
        source="fleet-clusters",
        snapshot_id=UUID(int=10),
        items=list(items),
    )


def openshift_collector(*items: EvidenceItem) -> StaticSourceCollector:
    return StaticSourceCollector(
        collector="openshift_inventory",
        source="openshift",
        snapshot_id=UUID(int=20),
        items=list(items),
    )


def tekton_collector(*items: EvidenceItem) -> StaticSourceCollector:
    return StaticSourceCollector(
        collector="tekton_inventory",
        source="tekton",
        snapshot_id=UUID(int=30),
        items=list(items),
    )


def definition(
    cluster_name: str,
    *,
    region: str = "us-east-2",
    status: str = "active",
) -> EvidenceItem:
    return EvidenceItem(
        item_id=UUID(int=11),
        kind="cluster_definition",
        data={
            "cluster_name": cluster_name,
            "path": (
                "archive"
                if status == "archived"
                else "provision"
            ) + f"/{cluster_name}",
            "status": status,
            "cloud_provider": "aws",
            "region": region,
            "tier": "base",
            "install_image_set": "image-set",
        },
    )


def actual_state(
    cluster_name: str,
    *,
    region: str = "us-east-2",
) -> EvidenceItem:
    return EvidenceItem(
        item_id=UUID(int=21),
        kind="cluster_actual_state",
        data={
            "cluster_name": cluster_name,
            "namespace": f"{cluster_name}-ns",
            "cloud_provider": "aws",
            "region": region,
            "install_image_set": "image-set",
            "cluster_deployment_conditions": [
                {"type": "Provisioned", "status": "True"}
            ],
            "managed_cluster_labels": {"tier": "base"},
        },
    )


def pipeline_run(cluster_name: str) -> EvidenceItem:
    return EvidenceItem(
        item_id=UUID(int=31),
        kind="pipeline_run",
        data={
            "cluster_name": cluster_name,
            "namespace": f"{cluster_name}-ns",
            "pipeline_run_name": "deploy-cluster",
            "pipeline_name": "deploy-cluster-pipeline",
            "status": "Failed",
            "start_time": "2026-01-01T00:00:00Z",
            "completion_time": "2026-01-01T00:05:00Z",
            "failed_task": {"name": "provision-task"},
        },
    )


def older_pipeline_run(cluster_name: str) -> EvidenceItem:
    item = pipeline_run(cluster_name)
    return item.model_copy(
        update={
            "item_id": UUID(int=32),
            "data": {
                **item.data,
                "pipeline_run_name": "deploy-cluster-old",
                "start_time": "2025-01-01T00:00:00Z",
                "completion_time": "2025-01-01T00:05:00Z",
            },
        }
    )


def test_cluster_inventory_collector_correlates_sources() -> None:
    collector = ClusterInventoryCollector(
        git_collector=git_collector(definition("example")),
        openshift_collector=openshift_collector(
            actual_state("example")
        ),
        tekton_collector=tekton_collector(
            pipeline_run("example"),
            older_pipeline_run("example"),
        ),
    )

    snapshot = collector.collect(
        command_id=UUID(int=1),
        snapshot_id=UUID(int=40),
        collected_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    assert snapshot.collector == "cluster_inventory"
    assert snapshot.source == "git+openshift+tekton"
    assert snapshot.source_version == (
        "git=fleet-clusters-version;"
        "openshift=openshift-version;"
        "tekton=tekton-version"
    )
    assert len(snapshot.items) == 5
    inventory = snapshot.items[-1]
    assert inventory.kind == "cluster_inventory"
    expected_state = {
        "cloud_provider": "aws",
        "region": "us-east-2",
        "tier": "base",
        "install_image_set": "image-set",
    }
    expected_run = pipeline_run("example").data
    run_fields = (
        "pipeline_run_name",
        "pipeline_name",
        "status",
        "start_time",
        "completion_time",
        "failed_task",
    )
    assert inventory.data == {
        "cluster_name": "example",
        "definition_status": "active",
        "tier": "base",
        "cloud_provider": "aws",
        "region": "us-east-2",
        "desired_state": expected_state,
        "actual_state": expected_state,
        "provisioning_status": "Provisioned",
        "latest_pipeline_run": {
            field: expected_run[field] for field in run_fields
        },
        "drift_indicator": "none",
    }


def test_cluster_inventory_collector_reports_drift_and_missing_sources() -> None:
    collector = ClusterInventoryCollector(
        git_collector=git_collector(
            definition("example", region="us-east-1"),
            definition("git-only"),
        ),
        openshift_collector=openshift_collector(
            actual_state("example"),
            actual_state("orphan"),
        ),
        tekton_collector=tekton_collector(
            pipeline_run("example")
        ),
    )

    snapshot = collector.collect(
        command_id=UUID(int=1),
        snapshot_id=UUID(int=40),
        collected_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    inventories = {
        item.data["cluster_name"]: item
        for item in snapshot.items
        if item.kind == "cluster_inventory"
    }
    assert inventories["example"].data["drift_indicator"] == "detected"
    assert inventories["git-only"].data["drift_indicator"] == (
        "missing-openshift"
    )
    assert inventories["git-only"].data["actual_state"] is None
    assert inventories["orphan"].data["drift_indicator"] == "missing-git"
    assert inventories["orphan"].data["desired_state"] is None


def test_cluster_inventory_collector_distinguishes_archived_clusters() -> None:
    collector = ClusterInventoryCollector(
        git_collector=git_collector(
            definition("archived-clean", status="archived"),
            definition("archived-with-resources", status="archived"),
        ),
        openshift_collector=openshift_collector(
            actual_state("archived-with-resources")
        ),
        tekton_collector=tekton_collector(),
    )

    snapshot = collector.collect(
        command_id=UUID(int=1),
        snapshot_id=UUID(int=40),
        collected_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    inventories = {
        item.data["cluster_name"]: item.data
        for item in snapshot.items
        if item.kind == "cluster_inventory"
    }
    assert inventories["archived-clean"][
        "definition_status"
    ] == "archived"
    assert inventories["archived-clean"]["drift_indicator"] == "archived"
    assert inventories["archived-with-resources"][
        "definition_status"
    ] == "archived"
    assert inventories["archived-with-resources"]["drift_indicator"] == (
        "archived-with-openshift-resources"
    )


def test_cli_inventory_list_combined_returns_view(
    tmp_path: Path,
    capsys,
) -> None:
    collector = StaticSourceCollector(
        collector="cluster_inventory",
        source="git+openshift+tekton",
        snapshot_id=UUID(int=40),
        items=[
            EvidenceItem(
                item_id=UUID(int=41),
                kind="cluster_inventory",
                data={"cluster_name": "example"},
            )
        ],
    )

    exit_code = main(
        [
            "inventory",
            "list",
            "--source",
            "combined",
            "--fleet-clusters",
            str(tmp_path),
            "--openshift-kubeconfig",
            str(tmp_path / "kubeconfig"),
            "--operator",
            "operator@example.com",
            "--idempotency-key",
            "test-key",
        ],
        collector=collector,
        command_id_factory=lambda: UUID(int=1),
    )

    assert exit_code == 0
    snapshot = json.loads(capsys.readouterr().out)
    assert snapshot["collector"] == "cluster_inventory"
    assert snapshot["items"][0]["data"]["cluster_name"] == "example"


def test_cli_inventory_list_combined_requires_both_sources(
    capsys,
) -> None:
    exit_code = main(
        [
            "inventory",
            "list",
            "--source",
            "combined",
            "--operator",
            "operator@example.com",
            "--idempotency-key",
            "test-key",
        ]
    )

    assert exit_code == 2
    assert "--fleet-clusters is required for --source combined" in (
        capsys.readouterr().err
    )
