from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, final
from uuid import UUID, uuid5

import aura.cli as cli
from aura.cli import main


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


def test_cli_workflow_inspect_uses_tekton_collector(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:
    kubeconfig = tmp_path / "aura-readonly"
    kubeconfig.write_text("{}", encoding="utf-8")
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
        command_id_factory=lambda: UUID(
            "00000000-0000-0000-0000-000000000001"
        ),
    )

    assert exit_code == 0
    captured = capsys.readouterr()
    snapshot = json.loads(captured.out)
    assert snapshot["collector"] == "tekton_inventory"
    assert snapshot["source"] == "tekton"
    assert snapshot["source_version"] == (
        "pipelineruns=789;taskruns=790"
    )
    assert snapshot["items"][0]["kind"] == "pipeline_run"
    assert captured.err == ""


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
    assert event["data"]["collector"] == "tekton_inventory"
