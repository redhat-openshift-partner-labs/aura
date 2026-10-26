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
    ("make_result", "expected"),
    [
        (
            lambda args: (
                CalledProcessError(
                    returncode=1,
                    cmd=args,
                    stderr="Error from server (Forbidden): denied\n",
                )
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
        (
            lambda args: "{",
            "oc returned invalid JSON for clusterdeployments",
        ),
    ],
)
def test_openshift_cli_client_converts_oc_failures(
    tmp_path: Path,
    make_result,
    expected: str,
) -> None:
    kubeconfig = tmp_path / "aura-readonly"
    kubeconfig.write_text("{}", encoding="utf-8")

    def failing_runner(args: list[str]) -> str:
        result = make_result(args)
        if isinstance(result, Exception):
            raise result
        return result

    client = OpenShiftCLIInventoryClient(
        kubeconfig_path=kubeconfig,
        command_runner=failing_runner,
    )

    with pytest.raises(OpenShiftCLIError) as error:
        client.list_cluster_deployments()

    assert str(error.value) == expected


def test_openshift_cli_client_rejects_oc_output_that_is_not_utf8(
    tmp_path: Path,
    monkeypatch,
) -> None:
    oc = tmp_path / "bin" / "oc"
    oc.parent.mkdir()
    oc.write_text("#!/bin/sh\nprintf '{\"items\": [\"\\377\"]}'\n")
    oc.chmod(0o755)
    monkeypatch.setenv("PATH", str(oc.parent))
    kubeconfig = tmp_path / "aura-readonly"
    kubeconfig.write_text("{}", encoding="utf-8")

    client = OpenShiftCLIInventoryClient(kubeconfig_path=kubeconfig)

    with pytest.raises(OpenShiftCLIError) as error:
        client.list_cluster_deployments()

    assert str(error.value) == (
        "oc returned output that is not UTF-8 for clusterdeployments"
    )


def test_openshift_cli_client_reports_only_the_last_error_line(
    tmp_path: Path,
) -> None:
    def failing_runner(args: list[str]) -> str:
        raise CalledProcessError(
            returncode=1,
            cmd=args,
            stderr=(
                "W1026 warning: deprecated flag\n"
                '{"partial": "json"\n'
                "Error from server (Forbidden): denied\n"
            ),
        )

    client = OpenShiftCLIInventoryClient(
        kubeconfig_path=tmp_path / "aura-readonly",
        command_runner=failing_runner,
    )

    with pytest.raises(OpenShiftCLIError) as error:
        client.list_cluster_deployments()

    assert str(error.value) == (
        "oc command failed for clusterdeployments "
        "(exit 1): Error from server (Forbidden): denied"
    )


@pytest.mark.parametrize(
    "body",
    ['{}', '[]', '{"kind": "Status"}', '{"items": null}', '{"items": {}}'],
)
def test_openshift_cli_client_rejects_a_response_without_an_item_list(
    tmp_path: Path,
    body: str,
) -> None:
    client = OpenShiftCLIInventoryClient(
        kubeconfig_path=tmp_path / "aura-readonly",
        command_runner=lambda args: body,
    )

    with pytest.raises(OpenShiftCLIError) as error:
        client.list_cluster_deployments()

    assert str(error.value) == (
        "oc returned a response without an item list for clusterdeployments"
    )
