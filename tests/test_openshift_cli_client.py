from __future__ import annotations

import json
from pathlib import Path
from subprocess import CalledProcessError, TimeoutExpired
from typing import final

import pytest

from aura.collectors.oc_cli import OpenShiftCLIError
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


def test_openshift_cli_client_converts_oc_failure(
    tmp_path: Path,
) -> None:
    kubeconfig = tmp_path / "aura-readonly"
    kubeconfig.write_text("{}", encoding="utf-8")

    def failing_runner(args: list[str]) -> str:
        raise CalledProcessError(
            returncode=1,
            cmd=args,
            stderr="Error from server (Forbidden): denied\n",
        )

    client = OpenShiftCLIInventoryClient(
        kubeconfig_path=kubeconfig,
        command_runner=failing_runner,
    )

    with pytest.raises(OpenShiftCLIError) as error:
        client.list_cluster_deployments()

    assert str(error.value) == (
        "oc command failed for clusterdeployments "
        "(exit 1): Error from server (Forbidden): denied"
    )


def test_openshift_cli_client_reports_missing_oc_executable(
    tmp_path: Path,
) -> None:
    kubeconfig = tmp_path / "aura-readonly"
    kubeconfig.write_text("{}", encoding="utf-8")

    def missing_runner(args: list[str]) -> str:
        raise FileNotFoundError("oc executable not found")

    client = OpenShiftCLIInventoryClient(
        kubeconfig_path=kubeconfig,
        command_runner=missing_runner,
    )

    with pytest.raises(OpenShiftCLIError) as error:
        client.list_cluster_deployments()

    assert str(error.value) == (
        "oc command failed for clusterdeployments: oc executable not found"
    )


def test_openshift_cli_client_reports_oc_timeout(tmp_path: Path) -> None:
    kubeconfig = tmp_path / "aura-readonly"
    kubeconfig.write_text("{}", encoding="utf-8")

    def timing_out_runner(args: list[str]) -> str:
        raise TimeoutExpired(cmd=args, timeout=30)

    client = OpenShiftCLIInventoryClient(
        kubeconfig_path=kubeconfig,
        command_runner=timing_out_runner,
    )

    with pytest.raises(OpenShiftCLIError) as error:
        client.list_cluster_deployments()

    assert str(error.value) == "oc command timed out for clusterdeployments"


def test_openshift_cli_client_reports_invalid_oc_json(
    tmp_path: Path,
) -> None:
    kubeconfig = tmp_path / "aura-readonly"
    kubeconfig.write_text("{}", encoding="utf-8")

    def invalid_json_runner(args: list[str]) -> str:
        return "{"

    client = OpenShiftCLIInventoryClient(
        kubeconfig_path=kubeconfig,
        command_runner=invalid_json_runner,
    )

    with pytest.raises(OpenShiftCLIError) as error:
        client.list_cluster_deployments()

    assert str(error.value) == "oc returned invalid JSON for clusterdeployments"
