from __future__ import annotations

import json
from pathlib import Path
from typing import final

from aura.collectors.openshift_cli_client import OpenShiftCLIInventoryClient


@final
class FakeCommandRunner:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str]) -> str:
        self.calls.append(args)
        if "clusterdeployments" in args:
            return json.dumps(
                {
                    "metadata": {"resourceVersion": "123"},
                    "items": [
                        {
                            "metadata": {
                                "name": "example",
                                "namespace": "example-ns",
                            }
                        }
                    ],
                }
            )
        if "managedclusters" in args:
            return json.dumps(
                {
                    "metadata": {"resourceVersion": "456"},
                    "items": [
                        {
                            "metadata": {
                                "name": "example",
                                "labels": {"tier": "base"},
                            }
                        }
                    ],
                }
            )
        raise AssertionError(f"unexpected command: {args}")


def test_openshift_cli_client_reads_inventory(tmp_path: Path) -> None:
    kubeconfig = tmp_path / "aura-readonly"
    kubeconfig.write_text("{}", encoding="utf-8")
    runner = FakeCommandRunner()
    client = OpenShiftCLIInventoryClient(
        kubeconfig_path=kubeconfig,
        command_runner=runner,
    )

    resource_version = client.resource_version()
    cluster_deployments = client.list_cluster_deployments()
    managed_clusters = client.list_managed_clusters()

    assert resource_version == "clusterdeployments=123;managedclusters=456"
    assert cluster_deployments == [
        {
            "metadata": {
                "name": "example",
                "namespace": "example-ns",
            }
        }
    ]
    assert managed_clusters == [
        {
            "metadata": {
                "name": "example",
                "labels": {"tier": "base"},
            }
        }
 ]
    assert runner.calls == [
        [
            "oc",
            "--kubeconfig",
            str(kubeconfig),
            "get",
            "clusterdeployments",
            "-A",
            "-o",
            "json",
        ],
        [
            "oc",
            "--kubeconfig",
            str(kubeconfig),
            "get",
            "managedclusters",
            "-o",
            "json",
        ],
    ]
