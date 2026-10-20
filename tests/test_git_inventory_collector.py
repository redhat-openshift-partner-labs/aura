from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from aura.collectors.git_inventory import GitInventoryCollector, _GitTree


def write_cluster(
    root: Path,
    *,
    directory: str = "provision",
    cluster_name: str = "example",
) -> None:
    cluster = root / directory / cluster_name
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
                f"  value: {cluster_name}",
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


def commit_repository(root: Path) -> str:
    subprocess.run(
        ["git", "-C", str(root), "init"],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "-C", str(root), "add", "-f", "."],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=Aura Tests",
            "-c",
            "user.email=aura@example.com",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "--no-verify",
            "-m",
            "fixture",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    revision = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return revision.stdout.strip()


def collect_snapshot(
    root: Path,
    *,
    source_version: str | None = None,
):
    return GitInventoryCollector(root=root).collect(
        command_id=UUID("00000000-0000-0000-0000-000000000001"),
        snapshot_id=UUID("00000000-0000-0000-0000-000000000050"),
        collected_at=datetime(2026, 1, 1, tzinfo=UTC),
        source_version=source_version,
    )


def test_git_inventory_collector_returns_typed_evidence(tmp_path: Path) -> None:
    write_cluster(tmp_path)
    revision = commit_repository(tmp_path)
    snapshot = collect_snapshot(tmp_path, source_version=revision)

    assert snapshot.collector == "git_inventory"
    assert snapshot.collector_version == "0.1.0"
    assert snapshot.source == "fleet-clusters"
    assert snapshot.source_version == revision
    assert len(snapshot.items) == 1

    item = snapshot.items[0]
    assert item.kind == "cluster_definition"
    assert item.data == {
        "path": "provision/example",
        "status": "active",
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


def test_git_inventory_collector_lists_active_and_archived_clusters(
    tmp_path: Path,
) -> None:
    write_cluster(tmp_path)
    write_cluster(
        tmp_path,
        directory="archive",
        cluster_name="archived-example",
    )
    commit_repository(tmp_path)
    snapshot = collect_snapshot(
        tmp_path,
        source_version="0123456789abcdef0123456789abcdef01234567",
    )

    assert [
        (item.data["cluster_name"], item.data["status"])
        for item in snapshot.items
    ] == [
        ("example", "active"),
        ("archived-example", "archived"),
    ]


def test_git_inventory_collector_reports_incomplete_cluster(tmp_path: Path) -> None:
    incomplete = tmp_path / "provision" / "incomplete"
    incomplete.mkdir(parents=True)
    (incomplete / "README.md").write_text(
        "Incomplete committed definition.\n",
        encoding="utf-8",
    )
    commit_repository(tmp_path)
    snapshot = collect_snapshot(
        tmp_path,
        source_version="0123456789abcdef0123456789abcdef01234567",
    )

    assert len(snapshot.items) == 1
    item = snapshot.items[0]
    assert item.kind == "cluster_definition_incomplete"
    assert item.data == {
        "path": "provision/incomplete",
        "status": "active",
        "cluster_name": "incomplete",
        "missing_files": [
            "crossplane/kustomization.yaml",
            "hive/patches/clusterdeployment.yaml",
            "hive/patches/managedcluster.yaml",
            "metadata.yaml",
        ],
    }


def test_git_inventory_collector_names_definition_without_metadata(
    tmp_path: Path,
) -> None:
    write_cluster(tmp_path, directory="archive", cluster_name="legacy")
    (tmp_path / "archive" / "legacy" / "metadata.yaml").unlink()
    commit_repository(tmp_path)

    snapshot = collect_snapshot(tmp_path)

    assert [(item.kind, item.data) for item in snapshot.items] == [
        (
            "cluster_definition_incomplete",
            {
                "path": "archive/legacy",
                "status": "archived",
                "cluster_name": "legacy",
                "missing_files": ["metadata.yaml"],
            },
        )
    ]


@pytest.mark.parametrize(
    ("relative_path", "content", "expected_error"),
    [
        (
            "metadata.yaml",
            "platform: [aws\n",
            "metadata.yaml: invalid YAML at line 2",
        ),
        (
            "metadata.yaml",
            "- aws\n",
            "metadata.yaml: expected a mapping",
        ),
        (
            "hive/patches/managedcluster.yaml",
            "tier: base\n",
            "hive/patches/managedcluster.yaml: "
            "expected a list of patch operations",
        ),
        (
            "hive/patches/clusterdeployment.yaml",
            "- op: replace\n  path: /metadata/name\n  value: broken\n",
            "hive/patches/clusterdeployment.yaml: "
            "missing patch path /spec/platform/aws/region",
        ),
        (
            "crossplane/kustomization.yaml",
            "resources: base\n",
            "crossplane/kustomization.yaml: "
            "expected resources to be a list of paths",
        ),
    ],
)
def test_git_inventory_collector_reports_invalid_definition(
    tmp_path: Path,
    relative_path: str,
    content: str,
    expected_error: str,
) -> None:
    write_cluster(tmp_path)
    write_cluster(tmp_path, cluster_name="broken")
    (tmp_path / "provision" / "broken" / relative_path).write_text(
        content,
        encoding="utf-8",
    )
    commit_repository(tmp_path)

    snapshot = collect_snapshot(tmp_path)

    items = {item.data["path"]: item for item in snapshot.items}
    assert items["provision/example"].kind == "cluster_definition"
    assert items["provision/broken"].kind == "cluster_definition_invalid"
    assert items["provision/broken"].data == {
        "path": "provision/broken",
        "status": "active",
        "cluster_name": "broken",
        "error": expected_error,
    }


def test_git_inventory_collector_reports_deprovision_sentinels(
    tmp_path: Path,
) -> None:
    write_cluster(tmp_path)
    deprovision = tmp_path / "deprovision"
    deprovision.mkdir()
    for name in ("README.md", "example", "other-archive"):
        (deprovision / name).write_text("", encoding="utf-8")
    commit_repository(tmp_path)

    snapshot = collect_snapshot(tmp_path)

    assert [
        (item.kind, item.data)
        for item in snapshot.items
        if item.kind == "deprovision_sentinel"
    ] == [
        (
            "deprovision_sentinel",
            {
                "path": "deprovision/example",
                "cluster_name": "example",
                "archive_suffix": False,
            },
        ),
        (
            "deprovision_sentinel",
            {
                "path": "deprovision/other-archive",
                "cluster_name": "other",
                "archive_suffix": True,
            },
        ),
    ]


def test_git_inventory_collector_reads_head_not_working_tree(
    tmp_path: Path,
) -> None:
    write_cluster(tmp_path)
    revision = commit_repository(tmp_path)
    (tmp_path / "provision" / "example" / "metadata.yaml").write_text(
        "platform: gcp\n",
        encoding="utf-8",
    )
    snapshot = collect_snapshot(tmp_path)

    assert snapshot.source_version == revision
    assert [item.data["cloud_provider"] for item in snapshot.items] == ["aws"]


def test_git_tree_pins_head_for_tree_and_blob_reads(
    tmp_path: Path,
) -> None:
    write_cluster(tmp_path)
    first_revision = commit_repository(tmp_path)
    tree = _GitTree.load(tmp_path)

    metadata = tmp_path / "provision" / "example" / "metadata.yaml"
    metadata.write_text("platform: gcp\n", encoding="utf-8")
    second_revision = commit_repository(tmp_path)

    assert second_revision != first_revision
    assert tree.revision == first_revision
    assert "platform: aws" in tree.file(
        "provision/example/metadata.yaml"
    )


def test_git_inventory_collector_ignores_untracked_cluster_directories(
    tmp_path: Path,
) -> None:
    write_cluster(tmp_path)
    commit_repository(tmp_path)
    write_cluster(tmp_path, cluster_name="uncommitted")
    snapshot = collect_snapshot(tmp_path)

    assert [item.data["cluster_name"] for item in snapshot.items] == ["example"]

