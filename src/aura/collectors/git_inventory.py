"""Read-only inventory collector for the fleet-clusters repository."""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, final
from uuid import UUID, uuid4, uuid5

import yaml

from aura.contracts import EvidenceItem, EvidenceSnapshot


class GitInventoryError(RuntimeError):
    """Raised when committed Git evidence cannot be read."""


@final
class _GitTree:
    """Read-only access to the committed tree at HEAD."""

    def __init__(
        self,
        *,
        root: Path,
        tracked_paths: frozenset[str],
        revision: str,
    ) -> None:
        self.root = root
        self.tracked_paths = tracked_paths
        self.revision = revision

    @classmethod
    def load(cls, root: Path) -> "_GitTree":
        revision = cls._git(root, ["rev-parse", "HEAD"]).strip()
        output = cls._git(
            root,
            ["ls-tree", "-r", "-z", "--name-only", revision],
        )
        return cls(
            root=root,
            tracked_paths=frozenset(
                path for path in output.split("\0") if path
            ),
            revision=revision,
        )

    def file(self, path: str) -> str:
        return self._git(self.root, ["show", f"{self.revision}:{path}"])

    def cluster_directories(self) -> list[tuple[str, str]]:
        return _cluster_directories(self.tracked_paths)

    def kustomizations(self, cluster_path: str) -> list[str]:
        return _kustomizations(self.tracked_paths, cluster_path)

    @staticmethod
    def _git(root: Path, arguments: list[str]) -> str:
        try:
            return _run_git(root, arguments)
        except subprocess.CalledProcessError as error:
            output = str(error.stderr or error.stdout or "").strip()
            detail = output.splitlines()[-1] if output else "no output"
            raise GitInventoryError(
                f"git command failed for fleet-clusters "
                f"(exit {error.returncode}): {detail}"
            ) from error
        except OSError as error:
            raise GitInventoryError(
                f"git checkout is not usable: {root}"
            ) from error

def _run_git(root: Path, arguments: list[str]) -> str:
    if not root.is_dir():
        raise GitInventoryError(f"git checkout is not usable: {root}")
    result = subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def _cluster_directories(
    tracked_paths: frozenset[str],
) -> list[tuple[str, str]]:
    statuses = {
        "provision": "active",
        "archive": "archived",
    }
    directories = {
        f"{parts[0]}/{parts[1]}": statuses[parts[0]]
        for tracked_path in tracked_paths
        if len(parts := tracked_path.split("/", maxsplit=2)) >= 3
        and parts[0] in statuses
    }
    directory_order = {"provision": 0, "archive": 1}
    return sorted(
        directories.items(),
        key=lambda item: (
            directory_order[item[0].split("/", maxsplit=1)[0]],
            item[0],
        ),
    )


def _kustomizations(
    tracked_paths: frozenset[str],
    cluster_path: str,
) -> list[str]:
    prefix = f"{cluster_path}/"
    names = {
        remainder.split("/", maxsplit=1)[0]
        for tracked_path in tracked_paths
        if tracked_path.startswith(prefix)
        and (
            remainder := tracked_path[len(prefix) :]
        ).count("/") == 1
        and remainder.endswith("/kustomization.yaml")
    }
    return sorted(names)


@final
class GitInventoryCollector:
    """Collects cluster definitions from a fleet-clusters Git checkout."""

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
        tree = _GitTree.load(self.root)
        resolved_source_version = source_version or self._git_revision(tree)
        items = [
            self._read_cluster(
                cluster_path,
                status=status,
                command_id=command_id,
                source_version=resolved_source_version,
                tree=tree,
            )
            for cluster_path, status in tree.cluster_directories()
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
        cluster_path: str,
        *,
        status: str,
        command_id: UUID,
        source_version: str,
        tree: _GitTree,
    ) -> EvidenceItem:
        relative_path = cluster_path
        missing_files = [
            required
            for required in self._required_files
            if f"{cluster_path}/{required}" not in tree.tracked_paths
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

        metadata = self._load_yaml(tree, f"{cluster_path}/metadata.yaml")
        cluster_patches = self._load_yaml(
            tree,
            f"{cluster_path}/hive/patches/clusterdeployment.yaml",
        )
        managed_patches = self._load_yaml(
            tree,
            f"{cluster_path}/hive/patches/managedcluster.yaml",
        )
        crossplane = self._load_yaml(
            tree,
            f"{cluster_path}/crossplane/kustomization.yaml",
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
            "kustomizations": tree.kustomizations(cluster_path),
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

    def _load_yaml(self, tree: _GitTree, path: str) -> Any:
        return yaml.safe_load(tree.file(path))

    @staticmethod
    def _patch_value(patches: Any, json_path: str) -> Any:
        for patch in patches:
            if patch.get("path") == json_path:
                return patch.get("value")
        raise ValueError(f"missing patch path: {json_path}")

    def _git_revision(self, tree: _GitTree) -> str:
        return tree.revision


__all__ = ["GitInventoryCollector", "GitInventoryError"]

