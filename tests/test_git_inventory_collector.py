from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from aura.collectors.git_inventory import GitInventoryCollector


def write_cluster(root: Path) -> None:
    cluster = root / "provision" / "example"
    metadata = cluster / "metadata.yaml"
    cluster_deployment = cluster / "hive" / "patches" / "clusterdeployment.yaml"
    managed_cluster = cluster / "hive" / "patches" / "managedcluster.yaml"
    hive_kustomization = cluster / "hive" / "kustomization.yaml"
    crossplane = cluster / "crossplane" / "kustomization.yaml"

    metadata.parent.mkdir(parents=True)
    cluster_deployment.parent.mkdir(parents=True)
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
    cluster_deployment.write_text(
        "\n".join(
            [
                "- op: replace",
                "  path: /metadata/name",
                "  value: example",
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


def test_git_inventory_collector_returns_typed_evidence(tmp_path: Path) -> None:
    write_cluster(tmp_path)
    collector = GitInventoryCollector(root=tmp_path)

    snapshot = collector.collect(
        command_id=UUID("00000000-0000-0000-0000-000000000001"),
        snapshot_id=UUID("00000000-0000-0000-0000-000000000050"),
        collected_at=datetime(2026, 1, 1, tzinfo=UTC),
        source_version="0123456789abcdef0123456789abcdef01234567",
    )

    assert snapshot.collector == "git_inventory"
    assert snapshot.collector_version == "0.1.0"
    assert snapshot.source == "fleet-clusters"
    assert snapshot.source_version == (
        "0123456789abcdef0123456789abcdef01234567"
    )
    assert len(snapshot.items) == 1

    item = snapshot.items[0]
    assert item.kind == "cluster_definition"
    assert item.data == {
        "path": "provision/example",
        "cluster_name": "example",
        "cloud_provider": "aws",
        "region": "us-east-2",
        "tier": "base",
        "environment": "development",
        "aws_credentials_mode": "scoped",
        "crossplane_template": "crossplane-scoped",
        "kustomizations": [
            "crossplane",
            "hive",
        ],
        "install_image_set": "img4.21.13-x86-64-appsub",
    }
    assert snapshot.redactions == []


def test_git_inventory_collector_reports_incomplete_cluster(tmp_path: Path) -> None:
    incomplete = tmp_path / "provision" / "incomplete"
    incomplete.mkdir(parents=True)
    collector = GitInventoryCollector(root=tmp_path)

    snapshot = collector.collect(
        command_id=UUID("00000000-0000-0000-0000-000000000001"),
        snapshot_id=UUID("00000000-0000-0000-0000-000000000050"),
        collected_at=datetime(2026, 1, 1, tzinfo=UTC),
        source_version="0123456789abcdef0123456789abcdef01234567",
    )

    assert len(snapshot.items) == 1
    item = snapshot.items[0]
    assert item.kind == "cluster_definition_incomplete"
    assert item.data == {
        "path": "provision/incomplete",
        "missing_files": [
            "crossplane/kustomization.yaml",
            "hive/patches/clusterdeployment.yaml",
            "hive/patches/managedcluster.yaml",
            "metadata.yaml",
        ],
    }

