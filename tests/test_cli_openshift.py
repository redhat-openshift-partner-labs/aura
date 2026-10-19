from __future__ import annotations

import json
from pathlib import Path
from typing import Any, final
from uuid import UUID

import aura.cli as cli
from aura.cli import main


@final
class FakeOpenShiftClient:
    def __init__(self, *, kubeconfig_path: Path) -> None:
        self.kubeconfig_path = kubeconfig_path

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
        return "clusterdeployments=123;managedclusters=456"


def test_cli_inventory_list_uses_openshift_collector(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:
    kubeconfig = tmp_path / "aura-readonly"
    kubeconfig.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(cli, "OpenShiftCLIInventoryClient", FakeOpenShiftClient)

    exit_code = main(
        [
            "inventory",
            "list",
            "--source",
            "openshift",
            "--openshift-kubeconfig",
            str(kubeconfig),
            "--operator",
            "operator@example.com",
            "--idempotency-key",
            "test-key",
        ],
        command_id_factory=lambda: UUID(
            "00000000-0000-0000-0000-000000000001"
        ),
    )

    assert exit_code == 0
    captured = capsys.readouterr()
    snapshot = json.loads(captured.out)
    assert snapshot["collector"] == "openshift_inventory"
    assert snapshot["source"] == "openshift"
    assert snapshot["source_version"] == (
        "clusterdeployments=123;managedclusters=456"
    )
    assert snapshot["items"][0]["kind"] == "cluster_actual_state"
    assert captured.err == ""
