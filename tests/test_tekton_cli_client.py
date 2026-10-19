from __future__ import annotations

import json
from pathlib import Path
from typing import final

from aura.collectors.openshift_cli_tekton_client import (
    OpenShiftCLITektonClient,
)


@final
class FakeCommandRunner:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str]) -> str:
        self.calls.append(args)
        if "pipelineruns" in args:
            return json.dumps(
                {
                    "metadata": {"resourceVersion": "789"},
                    "items": [
                        {
                            "metadata": {
                                "name": "deploy-cluster-abc",
                                "namespace": "example-ns",
                            }
                        }
                    ],
                }
            )
        if "taskruns" in args:
            return json.dumps(
                {
                    "metadata": {"resourceVersion": "790"},
                    "items": [
                        {
                            "metadata": {
                                "name": "provision-task-abc",
                                "namespace": "example-ns",
                                "labels": {
                                    "tekton.dev/pipelineRun": (
                                        "deploy-cluster-abc"
                                    )
                                },
                            }
                        }
                    ],
                }
            )
        raise AssertionError(f"unexpected command: {args}")


def test_openshift_cli_tekton_client_reads_workflow_status(
    tmp_path: Path,
) -> None:
    kubeconfig = tmp_path / "aura-readonly"
    kubeconfig.write_text("{}", encoding="utf-8")
    runner = FakeCommandRunner()
    client = OpenShiftCLITektonClient(
        kubeconfig_path=kubeconfig,
        command_runner=runner,
    )

    resource_version = client.resource_version()
    pipeline_runs = client.list_pipeline_runs()
    task_runs = client.list_task_runs()

    assert resource_version == "pipelineruns=789;taskruns=790"
    assert pipeline_runs[0]["metadata"]["name"] == "deploy-cluster-abc"
    assert task_runs[0]["metadata"]["name"] == "provision-task-abc"
    assert runner.calls == [
        [
            "oc",
            "--kubeconfig",
            str(kubeconfig),
            "get",
            "pipelineruns",
            "-A",
            "-o",
            "json",
        ],
        [
            "oc",
            "--kubeconfig",
            str(kubeconfig),
            "get",
            "taskruns",
            "-A",
            "-o",
            "json",
        ],
    ]


def test_openshift_cli_tekton_client_scopes_to_namespace(
    tmp_path: Path,
) -> None:
    kubeconfig = tmp_path / "aura-readonly"
    kubeconfig.write_text("{}", encoding="utf-8")
    runner = FakeCommandRunner()
    client = OpenShiftCLITektonClient(
        kubeconfig_path=kubeconfig,
        namespace="example-ns",
        command_runner=runner,
    )

    client.list_pipeline_runs()
    client.list_task_runs()

    assert runner.calls[0] == [
        "oc",
        "--kubeconfig",
        str(kubeconfig),
        "get",
        "pipelineruns",
        "-n",
        "example-ns",
        "-o",
        "json",
    ]
    assert runner.calls[1] == [
        "oc",
        "--kubeconfig",
        str(kubeconfig),
        "get",
        "taskruns",
        "-n",
        "example-ns",
        "-o",
        "json",
    ]
