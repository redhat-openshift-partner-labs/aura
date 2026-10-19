from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, final
from uuid import UUID

from aura.collectors.openshift_inventory import OpenShiftInventoryCollector


@final
class FakeOpenShiftClient:
    def list_cluster_deployments(self) -> list[dict[str, Any]]:
        return [
            {
                "metadata": {
                    "name": "example",
                    "namespace": "example-ns",
                },
                "spec": {
                    "platform": {
                        "aws": {
                            "region": "us-east-2",
                        },
                    },
                    "provisioning": {
                        "imageSetRef": {
                            "name": "img4.21.13-x86-64-appsub",
                        },
                    },
                },
                "status": {
                    "conditions": [
                        {
                            "type": "Provisioned",
                            "status": "True",
                            "reason": "Provisioned",
                        },
                    ],
                },
            },
        ]

    def list_managed_clusters(self) -> list[dict[str, Any]]:
        return [
            {
                "metadata": {
                    "name": "example",
                    "labels": {
                        "tier": "base",
                        "environment": "development",
                    },
                },
                "status": {
                    "conditions": [
                        {
                            "type": "ManagedClusterConditionAvailable",
                            "status": "True",
                            "reason": "ManagedClusterAvailable",
                        },
                    ],
                },
            },
        ]

    def resource_version(self) -> str:
        return "123"


@final
class FakeOpenShiftClientMissingManagedCluster:
    def list_cluster_deployments(self) -> list[dict[str, Any]]:
        return [
            {
                "metadata": {
                    "name": "example",
                    "namespace": "example-ns",
                },
                "spec": {},
            },
        ]

    def list_managed_clusters(self) -> list[dict[str, Any]]:
        return []

    def resource_version(self) -> str:
        return "123"


def test_openshift_inventory_collector_returns_cluster_actual_state() -> None:
    collector = OpenShiftInventoryCollector(client=FakeOpenShiftClient())

    snapshot = collector.collect(
        command_id=UUID("00000000-0000-0000-0000-000000000001"),
        snapshot_id=UUID("00000000-0000-0000-0000-000000000050"),
        collected_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    assert snapshot.collector == "openshift_inventory"
    assert snapshot.source == "openshift"
    assert snapshot.source_version == "123"
    assert len(snapshot.items) == 1

    item = snapshot.items[0]
    assert item.kind == "cluster_actual_state"
    assert item.data == {
        "cluster_name": "example",
        "namespace": "example-ns",
        "cloud_provider": "aws",
        "region": "us-east-2",
        "install_image_set": "img4.21.13-x86-64-appsub",
        "cluster_deployment_conditions": [
            {
                "type": "Provisioned",
                "status": "True",
                "reason": "Provisioned",
            },
        ],
        "managed_cluster_labels": {
            "tier": "base",
            "environment": "development",
        },
        "managed_cluster_conditions": [
            {
                "type": "ManagedClusterConditionAvailable",
                "status": "True",
                "reason": "ManagedClusterAvailable",
            },
        ],
    }


def test_openshift_inventory_collector_reports_missing_managed_cluster() -> None:
    collector = OpenShiftInventoryCollector(
        client=FakeOpenShiftClientMissingManagedCluster()
    )

    snapshot = collector.collect(
        command_id=UUID("00000000-0000-0000-0000-000000000001"),
        snapshot_id=UUID("00000000-0000-0000-0000-000000000050"),
        collected_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    item = snapshot.items[0]
    assert item.kind == "cluster_actual_state_incomplete"
    assert item.data == {
        "cluster_name": "example",
        "missing_managed_cluster": True,
    }
