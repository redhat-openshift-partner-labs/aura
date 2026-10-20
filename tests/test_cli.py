from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from subprocess import CalledProcessError
from typing import final
from uuid import UUID, uuid5

import pytest

import aura.collectors.git_inventory as git_inventory
from aura.cli import main
from aura.command_store import JsonCommandStore
from aura.collectors.git_inventory import GitInventoryCollector
from aura.contracts import CommandState, EvidenceSnapshot
from test_git_inventory_collector import commit_repository


FIXTURES = Path(__file__).parent / "fixtures"


@final
class FakeCollector:
    def collect(
        self,
        *,
        command_id: UUID,
        snapshot_id: UUID | None = None,
        collected_at: datetime | None = None,
        source_version: str | None = None,
    ) -> EvidenceSnapshot:
        return EvidenceSnapshot(
            snapshot_id=UUID(int=50),
            command_id=command_id,
            collector="git_inventory",
            collector_version="test",
            collected_at=datetime(2026, 1, 1, tzinfo=UTC),
            source="fleet-clusters",
            source_version="test",
            items=[],
            redactions=[],
        )


@final
class CountingCollector:
    def __init__(self) -> None:
        self._collector = FakeCollector()
        self.calls = 0

    def collect(
        self,
        *,
        command_id: UUID,
        snapshot_id: UUID | None = None,
        collected_at: datetime | None = None,
        source_version: str | None = None,
    ) -> EvidenceSnapshot:
        self.calls += 1
        return self._collector.collect(
            command_id=command_id,
            snapshot_id=snapshot_id,
            collected_at=collected_at,
            source_version=source_version,
        )


def inventory_arguments(
    tmp_path: Path,
    *,
    snapshot_dir: Path | None = None,
) -> list[str]:
    arguments = [
        "inventory",
        "list",
        "--fleet-clusters",
        str(tmp_path),
        "--operator",
        "operator@example.com",
        "--idempotency-key",
        "test-key",
        "--command-dir",
        str(tmp_path / "commands"),
    ]
    if snapshot_dir is not None:
        arguments.extend(["--snapshot-dir", str(snapshot_dir)])
    return arguments


def test_cli_inventory_list_suppresses_duplicate_idempotency_key(
    tmp_path,
    capsys,
) -> None:
    collector = CountingCollector()
    arguments = inventory_arguments(tmp_path)

    first_exit_code = main(
        arguments,
        collector=collector,
        command_id_factory=lambda: UUID(int=1),
    )
    first_captured = capsys.readouterr()
    second_exit_code = main(
        arguments,
        collector=collector,
        command_id_factory=lambda: UUID(int=2),
    )
    second_captured = capsys.readouterr()

    assert first_exit_code == 0
    assert first_captured.err == ""
    assert second_exit_code == 2
    assert second_captured.err == (
        "aura: idempotency key already succeeded: test-key\n"
    )
    assert second_captured.out == ""
    assert collector.calls == 1


def test_cli_inventory_list_rejects_key_reused_for_different_request(
    tmp_path,
    capsys,
) -> None:
    collector = CountingCollector()
    arguments = inventory_arguments(tmp_path)
    changed_arguments = [
        *arguments[:8],
        "--query",
        "different request",
        *arguments[8:],
    ]

    first_exit_code = main(
        arguments,
        collector=collector,
        command_id_factory=lambda: UUID(int=1),
    )
    capsys.readouterr()
    second_exit_code = main(
        changed_arguments,
        collector=collector,
        command_id_factory=lambda: UUID(int=2),
    )
    second_captured = capsys.readouterr()

    assert first_exit_code == 0
    assert second_exit_code == 2
    assert second_captured.err == (
        "aura: idempotency key belongs to a different request: test-key\n"
    )
    assert second_captured.out == ""
    assert collector.calls == 1


def test_cli_inventory_list_suppresses_concurrent_duplicate_key(
    tmp_path,
) -> None:
    collector = CountingCollector()
    arguments = inventory_arguments(tmp_path)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(
                main,
                arguments,
                collector=collector,
                command_id_factory=lambda: UUID(int=1),
            )
            for _ in range(2)
        ]
        exit_codes = [future.result() for future in futures]

    assert sorted(exit_codes) == [0, 2]
    assert collector.calls == 1


def test_cli_inventory_list_rejects_blank_operator(
    tmp_path,
    capsys,
) -> None:
    collector = CountingCollector()
    arguments = inventory_arguments(tmp_path)
    arguments[arguments.index("--operator") + 1] = " "

    exit_code = main(
        arguments,
        collector=collector,
        command_id_factory=lambda: UUID(int=1),
    )
    captured = capsys.readouterr()

    assert exit_code == 2
    assert captured.err == "aura: --operator must not be blank\n"
    assert captured.out == ""
    assert collector.calls == 0


def test_cli_inventory_list_rejects_blank_idempotency_key(
    tmp_path,
    capsys,
) -> None:
    collector = CountingCollector()
    arguments = inventory_arguments(tmp_path)
    arguments[arguments.index("--idempotency-key") + 1] = ""

    exit_code = main(
        arguments,
        collector=collector,
        command_id_factory=lambda: UUID(int=1),
    )
    captured = capsys.readouterr()

    assert exit_code == 2
    assert captured.err == "aura: --idempotency-key must not be blank\n"
    assert captured.out == ""
    assert collector.calls == 0


def test_cli_inventory_list_rejects_unusable_command_store(
    tmp_path,
    capsys,
) -> None:
    collector = CountingCollector()
    command_dir = tmp_path / "commands"
    command_dir.write_text("not a directory", encoding="utf-8")
    arguments = inventory_arguments(tmp_path)

    exit_code = main(
        arguments,
        collector=collector,
        command_id_factory=lambda: UUID(int=1),
    )
    captured = capsys.readouterr()

    assert exit_code == 2
    assert captured.err == (
        f"aura: --command-dir is not a usable directory: {command_dir}\n"
    )
    assert captured.out == ""
    assert collector.calls == 0


@pytest.mark.parametrize(
    "flag",
    [
        "--snapshot-dir",
        "--audit-dir",
        "--cwa-dir",
        "--diagnosis-dir",
    ],
)
def test_cli_inventory_list_rejects_unusable_record_directories(
    tmp_path,
    capsys,
    flag,
) -> None:
    collector = CountingCollector()
    invalid_directory = tmp_path / "not-a-directory"
    invalid_directory.write_text("not a directory", encoding="utf-8")
    arguments = inventory_arguments(tmp_path)
    arguments.extend([flag, str(invalid_directory)])
    if flag in {"--cwa-dir", "--diagnosis-dir"}:
        arguments.extend(["--audit-dir", str(tmp_path / "audit")])
    if flag == "--diagnosis-dir":
        arguments.extend(["--cwa-dir", str(tmp_path / "cwa")])

    exit_code = main(
        arguments,
        collector=collector,
        command_id_factory=lambda: UUID(int=1),
    )
    captured = capsys.readouterr()

    assert exit_code == 2
    assert captured.err == (
        f"aura: {flag} is not a usable directory: "
        f"{invalid_directory}\n"
    )
    assert captured.out == ""
    assert collector.calls == 0


def test_cli_inventory_list_reports_missing_git_checkout(
    tmp_path,
    capsys,
) -> None:
    fleet_clusters = tmp_path / "missing-checkout"
    arguments = inventory_arguments(tmp_path)
    arguments[arguments.index("--fleet-clusters") + 1] = str(fleet_clusters)

    exit_code = main(arguments, command_id_factory=lambda: UUID(int=1))
    captured = capsys.readouterr()

    assert exit_code == 2
    assert captured.err == (
        f"aura: git checkout is not usable: {fleet_clusters}\n"
    )
    assert captured.out == ""
    command = JsonCommandStore(tmp_path / "commands").load("test-key")
    assert command.status is CommandState.FAILED


def test_cli_inventory_list_reports_git_command_failure(
    tmp_path,
    capsys,
    monkeypatch,
) -> None:
    arguments = inventory_arguments(tmp_path)

    def failing_git(root, arguments):
        raise CalledProcessError(
            returncode=128,
            cmd=["git", *arguments],
            stderr="fatal: not a git repository\n",
        )

    monkeypatch.setattr(
        git_inventory._GitTree,
        "_run_git",
        staticmethod(failing_git),
    )

    exit_code = main(arguments, command_id_factory=lambda: UUID(int=1))
    captured = capsys.readouterr()

    assert exit_code == 2
    assert captured.err == (
        "aura: git command failed for fleet-clusters "
        "(exit 128): fatal: not a git repository\n"
    )
    assert captured.out == ""


def test_cli_inventory_list_writes_snapshot_json(
    tmp_path,
    capsys,
) -> None:
    exit_code = main(
        inventory_arguments(tmp_path),
        collector=FakeCollector(),
        command_id_factory=lambda: UUID(int=1),
    )

    assert exit_code == 0
    captured = capsys.readouterr()
    snapshot = json.loads(captured.out)
    command = JsonCommandStore(tmp_path / "commands").load("test-key")
    assert snapshot["command_id"] == str(UUID(int=1))
    assert snapshot["collector"] == "git_inventory"
    assert command.status is CommandState.SUCCEEDED
    assert captured.err == ""


def test_cli_inventory_list_persists_snapshot(
    tmp_path,
    capsys,
) -> None:
    snapshot_dir = tmp_path / "snapshots"

    exit_code = main(
        inventory_arguments(tmp_path, snapshot_dir=snapshot_dir),
        collector=FakeCollector(),
        command_id_factory=lambda: UUID(int=1),
    )

    assert exit_code == 0
    captured = capsys.readouterr()
    snapshot = json.loads(captured.out)
    snapshot_path = (
        snapshot_dir / f"{snapshot['snapshot_id']}.json"
    )

    assert snapshot_path.is_file()
    stored_snapshot = json.loads(
        snapshot_path.read_text(encoding="utf-8")
    )
    assert stored_snapshot == snapshot


def test_cli_inventory_list_records_audit_event(
    tmp_path,
    capsys,
) -> None:
    snapshot_dir = tmp_path / "snapshots"
    audit_dir = tmp_path / "audit"

    exit_code = main(
        [
            "inventory",
            "list",
            "--fleet-clusters",
            str(tmp_path),
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
        ],
        collector=FakeCollector(),
        command_id_factory=lambda: UUID(int=1),
    )

    assert exit_code == 0
    captured = capsys.readouterr()
    snapshot = json.loads(captured.out)
    event_files = list(audit_dir.glob("*.json"))
    assert len(event_files) == 2

    events = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(event_files)
    ]
    received_event = next(
        event
        for event in events
        if event["event_type"] == "command_received"
    )
    event = next(
        event
        for event in events
        if event["event_type"] == "evidence_collected"
    )
    expected_event_id = uuid5(
        UUID(int=1),
        f"evidence_collected:{snapshot['snapshot_id']}",
    )
    assert received_event["event_id"] == str(
        uuid5(UUID(int=1), "command_received")
    )
    assert received_event["previous_event_id"] is None
    assert event["event_id"] == str(expected_event_id)
    assert event["command_id"] == str(UUID(int=1))
    assert event["event_type"] == "evidence_collected"
    assert event["occurred_at"] == snapshot["collected_at"]
    assert event["actor"] == "operator@example.com"
    assert event["data"] == {
        "snapshot_id": snapshot["snapshot_id"],
        "snapshot_digest": snapshot["snapshot_digest"],
        "collector": snapshot["collector"],
        "source": snapshot["source"],
        "source_version": snapshot["source_version"],
        "persisted": True,
    }
    assert event["previous_event_id"] == received_event["event_id"]


def test_cli_inventory_list_assembles_and_persists_cwa(
    tmp_path,
    capsys,
) -> None:
    snapshot_dir = tmp_path / "snapshots"
    audit_dir = tmp_path / "audit"
    cwa_dir = tmp_path / "cwa"

    exit_code = main(
        [
            "inventory",
            "list",
            "--fleet-clusters",
            str(tmp_path),
            "--operator",
            "operator@example.com",
            "--idempotency-key",
            "test-key",
            "--command-dir",
            str(tmp_path / "commands"),
            "--query",
            "list clusters",
            "--snapshot-dir",
            str(snapshot_dir),
            "--audit-dir",
            str(audit_dir),
            "--cwa-dir",
            str(cwa_dir),
        ],
        collector=FakeCollector(),
        command_id_factory=lambda: UUID(int=1),
        clock=lambda: datetime(2026, 1, 1, tzinfo=UTC),
    )

    assert exit_code == 0
    captured = capsys.readouterr()
    evidence_snapshot = json.loads(captured.out)

    cwa_snapshot_files = list(
        (cwa_dir / "snapshots").glob("*.json")
    )
    cwa_trace_files = list((cwa_dir / "traces").glob("*.json"))
    assert len(cwa_snapshot_files) == 1
    assert len(cwa_trace_files) == 1

    cwa_snapshot = json.loads(
        cwa_snapshot_files[0].read_text(encoding="utf-8")
    )
    cwa_trace = json.loads(
        cwa_trace_files[0].read_text(encoding="utf-8")
    )
    assert cwa_snapshot["command_id"] == str(UUID(int=1))
    assert cwa_snapshot["evidence_snapshot_id"] == (
        evidence_snapshot["snapshot_id"]
    )
    assert cwa_snapshot["evidence_snapshot_digest"] == (
        evidence_snapshot["snapshot_digest"]
    )
    assert cwa_trace["cwa_snapshot_id"] == cwa_snapshot["snapshot_id"]
    assert cwa_trace["cwa_snapshot_digest"] == (
        cwa_snapshot["snapshot_digest"]
    )
    assert cwa_trace["evidence_snapshot_id"] == (
        evidence_snapshot["snapshot_id"]
    )

    event_files = list(audit_dir.glob("*.json"))
    assert len(event_files) == 3
    events = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in event_files
    ]
    evidence_event = next(
        event
        for event in events
        if event["event_type"] == "evidence_collected"
    )
    context_event = next(
        event
        for event in events
        if event["event_type"] == "context_assembled"
    )
    assert context_event["previous_event_id"] == (
        evidence_event["event_id"]
    )
    assert context_event["data"] == {
        "cwa_snapshot_id": cwa_snapshot["snapshot_id"],
        "cwa_snapshot_digest": cwa_snapshot["snapshot_digest"],
        "trace_id": cwa_trace["trace_id"],
        "evidence_snapshot_id": evidence_snapshot["snapshot_id"],
        "evidence_snapshot_digest": (
            evidence_snapshot["snapshot_digest"]
        ),
        "assembler_version": "0.1.0",
        "persisted": True,
    }


def test_cli_inventory_list_persists_diagnosis_and_model_audit(
    tmp_path,
    capsys,
) -> None:
    audit_dir = tmp_path / "audit"
    cwa_dir = tmp_path / "cwa"
    diagnosis_dir = tmp_path / "diagnoses"

    exit_code = main(
        [
            "inventory",
            "list",
            "--fleet-clusters",
            str(tmp_path),
            "--operator",
            "operator@example.com",
            "--idempotency-key",
            "test-key",
            "--command-dir",
            str(tmp_path / "commands"),
            "--query",
            "list clusters",
            "--audit-dir",
            str(audit_dir),
            "--cwa-dir",
            str(cwa_dir),
            "--diagnosis-dir",
            str(diagnosis_dir),
        ],
        collector=FakeCollector(),
        command_id_factory=lambda: UUID(int=1),
        clock=lambda: datetime(2026, 1, 1, tzinfo=UTC),
    )

    assert exit_code == 0
    capsys.readouterr()

    cwa_snapshot_files = list(
        (cwa_dir / "snapshots").glob("*.json")
    )
    cwa_trace_files = list((cwa_dir / "traces").glob("*.json"))
    diagnosis_files = list(diagnosis_dir.glob("*.json"))
    assert len(cwa_snapshot_files) == 1
    assert len(cwa_trace_files) == 1
    assert len(diagnosis_files) == 1

    cwa_snapshot = json.loads(
        cwa_snapshot_files[0].read_text(encoding="utf-8")
    )
    cwa_trace = json.loads(
        cwa_trace_files[0].read_text(encoding="utf-8")
    )
    diagnosis = json.loads(
        diagnosis_files[0].read_text(encoding="utf-8")
    )
    expected_diagnosis_id = uuid5(
        UUID(int=1),
        f"diagnosis:{cwa_snapshot['snapshot_id']}",
    )

    assert diagnosis["diagnosis_id"] == str(expected_diagnosis_id)
    assert diagnosis["cwa_snapshot_id"] == cwa_snapshot["snapshot_id"]
    assert diagnosis["cwa_snapshot_digest"] == (
        cwa_snapshot["snapshot_digest"]
    )
    assert diagnosis["provider_name"] == "aura"
    assert diagnosis["model_name"] == "deterministic-fallback"
    assert diagnosis["used_fallback"] is True
    assert diagnosis["response"]["summary"] == (
        "No model diagnosis is available."
    )
    assert cwa_trace["model_provider"] == "aura"
    assert cwa_trace["model_name"] == "deterministic-fallback"
    assert cwa_trace["response_digest"] == diagnosis["response_digest"]

    events = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in audit_dir.glob("*.json")
    ]
    assert len(events) == 4
    context_event = next(
        event
        for event in events
        if event["event_type"] == "context_assembled"
    )
    model_event = next(
        event
        for event in events
        if event["event_type"] == "model_called"
    )
    expected_model_event_id = uuid5(
        UUID(int=1),
        f"model_called:{expected_diagnosis_id}",
    )
    assert model_event["event_id"] == str(expected_model_event_id)
    assert model_event["previous_event_id"] == (
        context_event["event_id"]
    )
    assert model_event["data"] == {
        "diagnosis_id": str(expected_diagnosis_id),
        "response_digest": diagnosis["response_digest"],
        "cwa_snapshot_id": cwa_snapshot["snapshot_id"],
        "cwa_snapshot_digest": cwa_snapshot["snapshot_digest"],
        "trace_id": cwa_trace["trace_id"],
        "provider_name": "aura",
        "model_name": "deterministic-fallback",
        "used_fallback": True,
        "persisted": True,
    }


def test_cli_rejects_diagnosis_dir_without_cwa_dir(
    tmp_path,
    capsys,
) -> None:
    exit_code = main(
        [
            "inventory",
            "list",
            "--fleet-clusters",
            str(tmp_path),
            "--operator",
            "operator@example.com",
            "--idempotency-key",
            "test-key",
            "--command-dir",
            str(tmp_path / "commands"),
            "--audit-dir",
            str(tmp_path / "audit"),
            "--diagnosis-dir",
            str(tmp_path / "diagnoses"),
        ],
        collector=FakeCollector(),
        command_id_factory=lambda: UUID(int=1),
    )

    assert exit_code == 2
    captured = capsys.readouterr()
    assert captured.err == (
        "aura: --cwa-dir is required with --diagnosis-dir\n"
    )


def test_cli_inventory_list_matches_scoped_aws_fixture(
    tmp_path,
    capsys,
    monkeypatch,
) -> None:
    cluster = tmp_path / "provision" / "0171d46a-scopedaws"
    metadata = cluster / "metadata.yaml"
    hive_kustomization = cluster / "hive" / "kustomization.yaml"
    cluster_deployment = (
        cluster / "hive" / "patches" / "clusterdeployment.yaml"
    )
    managed_cluster = cluster / "hive" / "patches" / "managedcluster.yaml"
    crossplane = cluster / "crossplane" / "kustomization.yaml"

    metadata.parent.mkdir(parents=True)
    hive_kustomization.parent.mkdir(parents=True)
    cluster_deployment.parent.mkdir(parents=True)
    crossplane.parent.mkdir(parents=True)

    metadata.write_text(
        "\n".join(
            [
                "platform: aws",
                "aws-credentials-mode: scoped",
                "htpasswd-provider-name: Red Hat",
            ]
        ),
        encoding="utf-8",
    )
    hive_kustomization.write_text(
        "\n".join(
            [
                "apiVersion: kustomize.config.k8s.io/v1beta1",
                "kind: Kustomization",
                "resources:",
                "- patches",
            ]
        ),
        encoding="utf-8",
    )
    cluster_deployment.write_text(
        "\n".join(
            [
                "- op: replace",
                "  path: /metadata/name",
                "  value: 0171d46a-scopedaws",
                "- op: replace",
                "  path: /spec/platform/aws/region",
                "  value: us-east-2",
                "- op: replace",
                "  path: /spec/provisioning/imageSetRef/name",
                "  value: img4.21.13-x86-64-appsub",
            ]
        ),
        encoding="utf-8",
    )
    managed_cluster.write_text(
        "\n".join(
            [
                "- op: add",
                "  path: /metadata/labels/tier",
                "  value: base",
                "- op: add",
                "  path: /metadata/labels/environment",
                "  value: development",
            ]
        ),
        encoding="utf-8",
    )
    crossplane.write_text(
        "\n".join(
            [
                "apiVersion: kustomize.config.k8s.io/v1beta1",
                "kind: Kustomization",
                "resources:",
                "- ../../../cluster-templates/aws/standard/crossplane-scoped",
            ]
        ),
        encoding="utf-8",
    )
    commit_repository(tmp_path)

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz):
            return datetime(2026, 10, 19, tzinfo=tz)

    monkeypatch.setattr(git_inventory, "datetime", FixedDateTime)
    monkeypatch.setattr(
        git_inventory,
        "uuid4",
        lambda: UUID("00000000-0000-0000-0000-000000000050"),
    )
    monkeypatch.setattr(
        GitInventoryCollector,
        "_git_revision",
        lambda self, tree: (
            "492bc8e07afddcd557441ddb7d72158de60ab8cb"
        ),
    )

    exit_code = main(
        [
            "inventory",
            "list",
            "--fleet-clusters",
            str(tmp_path),
            "--operator",
            "operator@example.com",
            "--idempotency-key",
            "test-key",
            "--command-dir",
            str(tmp_path / "commands"),
        ],
        command_id_factory=lambda: UUID(
            "00000000-0000-0000-0000-000000000001"
        ),
    )

    assert exit_code == 0
    captured = capsys.readouterr()
    expected = json.loads(
        (
            FIXTURES / "fleet-clusters-inventory.json"
        ).read_text(encoding="utf-8")
    )
    actual = json.loads(captured.out)
    assert actual == expected
