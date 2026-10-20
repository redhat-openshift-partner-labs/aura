from __future__ import annotations

import json
from pathlib import Path
from typing import Any, final
from uuid import UUID, uuid5

import aura.cli as cli
from aura.cli import main
from aura.collectors.oc_cli import OpenShiftCLIError
from test_cluster_inventory_collector import (
    definition,
    git_collector,
)
from test_openshift_inventory_collector import FakeOpenShiftClient


@final
class FakeOpenShiftCLITektonClient:
    def __init__(
        self,
        *,
        kubeconfig_path: Path,
        namespace: str | None = None,
        command_runner: object | None = None,
    ) -> None:
        self.kubeconfig_path = kubeconfig_path
        self.namespace = namespace

    def list_pipeline_runs(self) -> list[dict[str, Any]]:
        return [
            {
                "metadata": {
                    "name": "deploy-cluster-abc",
                    "namespace": "example-ns",
                },
                "spec": {
                    "pipelineRef": {"name": "deploy-cluster"},
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
        return []

    def resource_version(self) -> str:
        return "pipelineruns=789;taskruns=790"


def fake_inventory_client(**kwargs: object) -> FakeOpenShiftClient:
    return FakeOpenShiftClient()


@final
class FailingInventoryClient:
    def __init__(self, **kwargs: object) -> None:
        pass

    def resource_version(self) -> str:
        raise OpenShiftCLIError(
            "oc command failed for clusterdeployments "
            "(exit 1): Error from server (Forbidden): denied"
        )

    def list_cluster_deployments(self) -> list[dict[str, Any]]:
        return []

    def list_managed_clusters(self) -> list[dict[str, Any]]:
        return []


def fake_git_collector(root: Path):
    return git_collector(definition("example"))


def run_workflow_inspect(
    tmp_path: Path,
    capsys,
    monkeypatch,
    *,
    with_git: bool,
) -> tuple[int, dict, str]:
    kubeconfig = tmp_path / "aura-readonly"
    kubeconfig.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        cli,
        "OpenShiftCLIInventoryClient",
        fake_inventory_client,
    )
    monkeypatch.setattr(
        cli,
        "OpenShiftCLITektonClient",
        FakeOpenShiftCLITektonClient,
    )
    arguments = [
        "workflow",
        "inspect",
        "--openshift-kubeconfig",
        str(kubeconfig),
    ]
    if with_git:
        monkeypatch.setattr(
            cli,
            "GitInventoryCollector",
            fake_git_collector,
        )
        arguments.extend(["--fleet-clusters", str(tmp_path)])
    arguments.extend(
        [
            "--operator",
            "operator@example.com",
            "--idempotency-key",
            "test-key",
        ]
    )
    exit_code = main(
        arguments,
        command_id_factory=lambda: UUID(
            "00000000-0000-0000-0000-000000000001"
        ),
    )
    captured = capsys.readouterr()
    return exit_code, json.loads(captured.out), captured.err


def test_cli_workflow_inspect_uses_correlated_collector(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:
    exit_code, snapshot, stderr = run_workflow_inspect(
        tmp_path,
        capsys,
        monkeypatch,
        with_git=False,
    )

    assert exit_code == 0
    assert snapshot["collector"] == "workflow_inventory"
    assert snapshot["source"] == "openshift+tekton"
    assert snapshot["source_version"] == (
        "openshift=123;tekton=pipelineruns=789;taskruns=790"
    )
    assert snapshot["items"][0]["kind"] == "cluster_actual_state"
    assert snapshot["items"][-1]["kind"] == "workflow_view"
    assert stderr == ""


def test_cli_workflow_inspect_reports_oc_failure(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:
    kubeconfig = tmp_path / "aura-readonly"
    kubeconfig.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        cli,
        "OpenShiftCLIInventoryClient",
        FailingInventoryClient,
    )
    monkeypatch.setattr(
        cli,
        "OpenShiftCLITektonClient",
        FakeOpenShiftCLITektonClient,
    )

    exit_code = main(
        [
            "workflow",
            "inspect",
            "--openshift-kubeconfig",
            str(kubeconfig),
            "--operator",
            "operator@example.com",
            "--idempotency-key",
            "test-key",
        ],
        command_id_factory=lambda: UUID(int=1),
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert captured.err == (
        "aura: oc command failed for clusterdeployments "
        "(exit 1): Error from server (Forbidden): denied\n"
    )
    assert captured.out == ""


def test_cli_workflow_inspect_returns_correlated_view(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:
    exit_code, snapshot, _ = run_workflow_inspect(
        tmp_path,
        capsys,
        monkeypatch,
        with_git=True,
    )

    assert exit_code == 0
    assert snapshot["collector"] == "workflow_inventory"
    assert snapshot["source"] == "git+openshift+tekton"
    assert snapshot["source_version"] == (
        "git=fleet-clusters-version;openshift=123;"
        "tekton=pipelineruns=789;taskruns=790"
    )
    view = snapshot["items"][-1]
    assert view["kind"] == "workflow_view"
    assert view["data"]["related_cluster"] == "example"
    assert view["data"]["next_diagnostic_step"] == (
        "Inspect PipelineRun conditions and events."
    )


def test_cli_workflow_inspect_persists_snapshot_and_audit(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:
    kubeconfig = tmp_path / "aura-readonly"
    kubeconfig.write_text("{}", encoding="utf-8")
    snapshot_dir = tmp_path / "snapshots"
    audit_dir = tmp_path / "audit"
    monkeypatch.setattr(
        cli,
        "OpenShiftCLIInventoryClient",
        fake_inventory_client,
    )
    monkeypatch.setattr(
        cli,
        "OpenShiftCLITektonClient",
        FakeOpenShiftCLITektonClient,
    )

    exit_code = main(
        [
            "workflow",
            "inspect",
            "--openshift-kubeconfig",
            str(kubeconfig),
            "--operator",
            "operator@example.com",
            "--idempotency-key",
            "test-key",
            "--snapshot-dir",
            str(snapshot_dir),
            "--audit-dir",
            str(audit_dir),
        ],
        command_id_factory=lambda: UUID(
            "00000000-0000-0000-0000-000000000001"
        ),
    )

    assert exit_code == 0
    captured = capsys.readouterr()
    snapshot = json.loads(captured.out)
    snapshot_path = (
        snapshot_dir / f"{snapshot['snapshot_id']}.json"
    )
    event_files = list(audit_dir.glob("*.json"))

    assert snapshot_path.is_file()
    assert json.loads(snapshot_path.read_text(encoding="utf-8")) == snapshot
    assert len(event_files) == 1
    event = json.loads(event_files[0].read_text(encoding="utf-8"))
    expected_event_id = uuid5(
        UUID("00000000-0000-0000-0000-000000000001"),
        f"evidence_collected:{snapshot['snapshot_id']}",
    )
    assert event["event_id"] == str(expected_event_id)
    assert event["command_id"] == str(
        UUID("00000000-0000-0000-0000-000000000001")
    )
    assert event["event_type"] == "evidence_collected"
    assert event["data"]["collector"] == "workflow_inventory"
