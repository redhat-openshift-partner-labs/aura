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


@pytest.mark.parametrize(
    ("make_error", "expected"),
    [
        (
            lambda args: CalledProcessError(
                returncode=1,
                cmd=args,
                stderr="Error from server (Forbidden): denied\n",
            ),
            "oc command failed for clusterdeployments "
            "(exit 1): Error from server (Forbidden): denied",
        ),
        (
            lambda args: FileNotFoundError("oc executable not found"),
            "oc command failed for clusterdeployments: "
            "oc executable not found",
        ),
        (
            lambda args: TimeoutExpired(cmd=args, timeout=30),
            "oc command timed out for clusterdeployments",
        ),
    ],
)
def test_openshift_cli_client_converts_oc_process_errors(
    tmp_path: Path,
    make_error,
    expected: str,
) -> None:
    kubeconfig = tmp_path / "aura-readonly"
    kubeconfig.write_text("{}", encoding="utf-8")

    def failing_runner(args: list[str]) -> str:
        raise make_error(args)

    client = OpenShiftCLIInventoryClient(
        kubeconfig_path=kubeconfig,
        command_runner=failing_runner,
    )

    with pytest.raises(OpenShiftCLIError) as error:
        client.list_cluster_deployments()

    assert str(error.value) == expected


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
