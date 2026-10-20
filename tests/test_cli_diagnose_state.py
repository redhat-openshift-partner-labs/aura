from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import final
from uuid import UUID

from aura.cli import main
from aura.cli import build_parser
from aura.command_store import JsonCommandStore
from aura.collectors.cluster_state import ClusterStateCollector
from aura.contracts import CommandState, EvidenceSnapshot
from test_cluster_state_collector import (
    EmptyCollector,
    FakeGitCollector,
    FakeOpenShiftCollector,
    FakeTektonCollector,
)


@final
class FakeClusterStateCollector:
    def __init__(self, *, complete: bool = True) -> None:
        if complete:
            git_collector = FakeGitCollector()
            openshift_collector = FakeOpenShiftCollector()
            tekton_collector = FakeTektonCollector()
        else:
            git_collector = EmptyCollector()
            openshift_collector = EmptyCollector()
            tekton_collector = EmptyCollector()
        self._collector = ClusterStateCollector(
            git_collector=git_collector,
            openshift_collector=openshift_collector,
            tekton_collector=tekton_collector,
            cluster_name="example",
        )


    def collect(
        self,
        *,
        command_id: UUID,
        snapshot_id: UUID | None = None,
        collected_at: datetime | None = None,
        source_version: str | None = None,
    ) -> EvidenceSnapshot:
        return self._collector.collect(
            command_id=command_id,
            snapshot_id=snapshot_id or UUID(int=40),
            collected_at=collected_at or datetime(2026, 1, 1, tzinfo=UTC),
            source_version=source_version,
        )


def run_diagnose_state(
    tmp_path: Path,
    collector: FakeClusterStateCollector,
) -> tuple[int, Path, Path, Path, Path]:
    snapshot_dir = tmp_path / "snapshots"
    audit_dir = tmp_path / "audit"
    cwa_dir = tmp_path / "cwa"
    diagnosis_dir = tmp_path / "diagnoses"
    exit_code = main(
        [
            "diagnose",
            "state",
            "--cluster",
            "example",
            "--fleet-clusters",
            str(tmp_path),
            "--openshift-kubeconfig",
            str(tmp_path / "kubeconfig"),
            "--operator",
            "operator@example.com",
            "--idempotency-key",
            "test-key",
            "--command-dir",
            str(tmp_path / "commands"),
            "--snapshot-dir",
            str(snapshot_dir),
            "--audit-dir",
            str(audit_dir),
            "--cwa-dir",
            str(cwa_dir),
            "--diagnosis-dir",
            str(diagnosis_dir),
        ],
        collector=collector,
        command_id_factory=lambda: UUID(int=1),
        clock=lambda: datetime(2026, 1, 1, tzinfo=UTC),
    )
    return exit_code, snapshot_dir, audit_dir, cwa_dir, diagnosis_dir


def test_cli_diagnose_state_parses_pipeline_run() -> None:
    args = build_parser().parse_args(
        [
            "diagnose",
            "state",
            "--cluster",
            "example",
            "--fleet-clusters",
            "/tmp/fleet-clusters",
            "--openshift-kubeconfig",
            "/tmp/kubeconfig",
            "--cwa-dir",
            "/tmp/cwa",
            "--operator",
            "operator@example.com",
            "--idempotency-key",
            "test-key",
            "--command-dir",
            "/tmp/commands",
            "--pipeline-run",
            "deploy-cluster-abc",
        ]
    )

    assert args.pipeline_run == "deploy-cluster-abc"


def test_cli_diagnose_state_rejects_blank_pipeline_run(
    capsys,
) -> None:
    exit_code = main(
        [
            "diagnose",
            "state",
            "--cluster",
            "example",
            "--fleet-clusters",
            ".",
            "--openshift-kubeconfig",
            ".",
            "--cwa-dir",
            ".",
            "--audit-dir",
            ".",
            "--operator",
            "operator@example.com",
            "--idempotency-key",
            "test-key",
            "--command-dir",
            ".",
            "--pipeline-run",
            " ",
        ],
        collector=FakeClusterStateCollector(),
        command_id_factory=lambda: UUID(int=1),
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert captured.err == (
        "aura: --pipeline-run must not be blank\n"
    )
    assert captured.out == ""


def test_cli_diagnose_state_returns_cluster_diagnosis(
    tmp_path: Path,
    capsys,
) -> None:
    exit_code, snapshot_dir, audit_dir, cwa_dir, diagnosis_dir = (
        run_diagnose_state(tmp_path, FakeClusterStateCollector())
    )

    assert exit_code == 0
    captured = capsys.readouterr()
    answer = json.loads(captured.out)
    diagnosis = answer["diagnosis"]
    response = diagnosis["response"]
    assert response["summary"] == (
        "Cluster example is defined and deployed, but its latest workflow failed."
    )
    assert response["recommended_inspection"]["description"] == (
        "Inspect the failed TaskRun provision-task-abc."
    )
    assert answer["evidence_snapshot_id"] == str(UUID(int=40))
    assert answer["evidence_snapshot_digest"] == json.loads(
        next(snapshot_dir.glob("*.json")).read_text()
    )["snapshot_digest"]
    assert answer["cwa_trace_id"] == json.loads(
        next((cwa_dir / "traces").glob("*.json")).read_text()
    )["trace_id"]
    assert diagnosis["diagnosis_id"] == json.loads(
        next(diagnosis_dir.glob("*.json")).read_text()
    )["diagnosis_id"]
    assert diagnosis["provider_name"] == "aura"
    assert diagnosis["model_name"] == "cluster-state-deterministic"

    assert len(list(snapshot_dir.glob("*.json"))) == 1
    assert len(list(audit_dir.glob("*.json"))) == 4
    assert len(list((cwa_dir / "snapshots").glob("*.json"))) == 1
    assert len(list((cwa_dir / "traces").glob("*.json"))) == 1
    assert len(list(diagnosis_dir.glob("*.json"))) == 1
    command = JsonCommandStore(tmp_path / "commands").load("test-key")
    assert command.status is CommandState.SUCCEEDED


def test_cli_diagnose_state_refuses_missing_evidence(
    tmp_path: Path,
    capsys,
) -> None:
    exit_code, snapshot_dir, audit_dir, cwa_dir, diagnosis_dir = (
        run_diagnose_state(
            tmp_path,
            FakeClusterStateCollector(complete=False),
        )
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "diagnosis refused" in captured.err
    assert "Git cluster definition" in captured.err
    assert captured.out == ""
    assert len(list(snapshot_dir.glob("*.json"))) == 1
    assert len(list(audit_dir.glob("*.json"))) == 4
    assert len(list((cwa_dir / "snapshots").glob("*.json"))) == 1
    assert len(list((cwa_dir / "traces").glob("*.json"))) == 1
    assert list(diagnosis_dir.glob("*.json")) == []
    command = JsonCommandStore(tmp_path / "commands").load("test-key")
    assert command.status is CommandState.REFUSED
    cwa_snapshot = json.loads(
        next((cwa_dir / "snapshots").glob("*.json")).read_text()
    )
    state_task = next(
        slot
        for slot in cwa_snapshot["slots"]
        if slot["name"] == "state.task"
    )
    assert state_task["content"]["status"] == "assembling_context"
    event_types = {
        json.loads(path.read_text())["event_type"]
        for path in audit_dir.glob("*.json")
    }
    assert event_types == {
        "command_received",
        "evidence_collected",
        "context_assembled",
        "command_refused",
    }


def test_cli_diagnose_state_suppresses_duplicate_idempotency_key(
    tmp_path: Path,
    capsys,
) -> None:
    collector = FakeClusterStateCollector()

    first_exit_code, _, _, _, _ = run_diagnose_state(
        tmp_path,
        collector,
    )
    first_captured = capsys.readouterr()
    second_exit_code, _, _, _, _ = run_diagnose_state(
        tmp_path,
        collector,
    )
    second_captured = capsys.readouterr()

    assert first_exit_code == 0
    assert first_captured.err == ""
    assert second_exit_code == 2
    assert second_captured.err == (
        "aura: idempotency key already succeeded: test-key\n"
    )
    assert second_captured.out == ""
