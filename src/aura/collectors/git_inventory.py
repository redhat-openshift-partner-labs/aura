"""Read-only inventory collector for the fleet-clusters repository."""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4, uuid5

import yaml

from aura.contracts import EvidenceItem, EvidenceSnapshot


class GitInventoryCollector:
    """Collects cluster definitions from a fleet-clusters working tree."""

    collector_version = "0.1.0"
    _required_files = (
        "crossplane/kustomization.yaml",
        "hive/patches/clusterdeployment.yaml",
        "hive/patches/managedcluster.yaml",
        "metadata.yaml",
    )

    def __init__(self, root: Path) -> None:
        self.root = root

    def collect(
        self,
        *,
        command_id: UUID,
        snapshot_id: UUID | None = None,
        collected_at: datetime | None = None,
        source_version: str | None = None,
    ) -> EvidenceSnapshot:
        """Return a typed inventory snapshot for all cluster definitions."""

        resolved_snapshot_id = snapshot_id or uuid4()
        resolved_collected_at = collected_at or datetime.now(UTC)
        resolved_source_version = source_version or self._git_revision()
        items = [
            self._read_cluster(
                cluster_path,
                status=status,
                command_id=command_id,
                source_version=resolved_source_version,
            )
            for directory, status in (
                ("provision", "active"),
                ("archive", "archived"),
            )
            if (self.root / directory).is_dir()
            for cluster_path in sorted(
                (self.root / directory).iterdir()
            )
            if cluster_path.is_dir()
        ]

        return EvidenceSnapshot(
            snapshot_id=resolved_snapshot_id,
            command_id=command_id,
            collector="git_inventory",
            collector_version=self.collector_version,
            collected_at=resolved_collected_at,
            source="fleet-clusters",
            source_version=resolved_source_version,
            items=items,
            redactions=[],
        )

    def _read_cluster(
        self,
        cluster_path: Path,
        *,
        status: str,
        command_id: UUID,
        source_version: str,
    ) -> EvidenceItem:
        relative_path = cluster_path.relative_to(self.root).as_posix()
        missing_files = [
            required
            for required in self._required_files
            if not (cluster_path / required).is_file()
        ]
        if missing_files:
            return EvidenceItem(
                item_id=uuid5(command_id, f"{source_version}:{relative_path}"),
                kind="cluster_definition_incomplete",
                data={
                    "path": relative_path,
                    "status": status,
                    "missing_files": missing_files,
                },
            )

        metadata = self._load_yaml(cluster_path / "metadata.yaml")
        cluster_patches = self._load_yaml(
            cluster_path / "hive" / "patches" / "clusterdeployment.yaml"
        )
        managed_patches = self._load_yaml(
            cluster_path / "hive" / "patches" / "managedcluster.yaml"
        )
        crossplane = self._load_yaml(
            cluster_path / "crossplane" / "kustomization.yaml"
        )

        resources = crossplane.get("resources", [])
        crossplane_template = Path(resources[0]).name if resources else ""

        data = {
            "path": relative_path,
            "status": status,
            "cluster_name": self._patch_value(cluster_patches, "/metadata/name"),
            "cloud_provider": metadata.get("platform"),
            "region": self._patch_value(
                cluster_patches,
                "/spec/platform/aws/region",
            ),
            "tier": self._patch_value(
                managed_patches,
                "/metadata/labels/tier",
            ),
            "environment": self._patch_value(
                managed_patches,
                "/metadata/labels/environment",
            ),
            "aws_credentials_mode": metadata.get("aws-credentials-mode"),
            "crossplane_template": crossplane_template,
            "kustomizations": self._kustomizations(cluster_path),
            "install_image_set": self._patch_value(
                cluster_patches,
                "/spec/provisioning/imageSetRef/name",
            ),
        }

        return EvidenceItem(
            item_id=uuid5(command_id, f"{source_version}:{relative_path}"),
            kind="cluster_definition",
            data=data,
        )

    @staticmethod
    def _load_yaml(path: Path) -> Any:
        with path.open(encoding="utf-8") as stream:
            return yaml.safe_load(stream)

    @staticmethod
    def _kustomizations(cluster_path: Path) -> list[str]:
        return sorted(
            path.name
            for path in cluster_path.iterdir()
            if path.is_dir() and (path / "kustomization.yaml").is_file()
        )

    @staticmethod
    def _patch_value(patches: Any, json_path: str) -> Any:
        for patch in patches:
            if patch.get("path") == json_path:
                return patch.get("value")
        raise ValueError(f"missing patch path: {json_path}")

    def _git_revision(self) -> str:
        result = subprocess.run(
            ["git", "-C", str(self.root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()


__all__ = ["GitInventoryCollector"]

