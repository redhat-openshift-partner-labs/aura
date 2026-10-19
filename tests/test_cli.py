from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import final
from uuid import UUID

import aura.collectors.git_inventory as git_inventory
from aura.cli import main
from aura.collectors.git_inventory import GitInventoryCollector
from aura.contracts import EvidenceSnapshot


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


def test_cli_inventory_list_writes_snapshot_json(
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
        ],
        collector=FakeCollector(),
        command_id_factory=lambda: UUID(int=1),
    )

    assert exit_code == 0
    captured = capsys.readouterr()
    snapshot = json.loads(captured.out)
    assert snapshot["command_id"] == str(UUID(int=1))
    assert snapshot["collector"] == "git_inventory"
    assert captured.err == ""


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
        lambda self: "492bc8e07afddcd557441ddb7d72158de60ab8cb",
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
    del actual["items"][0]["item_id"]
    del expected["items"][0]["item_id"]
    assert actual == expected
