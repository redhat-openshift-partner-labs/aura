"""Read-only inventory collector for the fleet-clusters repository."""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, final
from uuid import UUID, uuid4, uuid5

import yaml

from aura.contracts import ARCHIVE_SUFFIX, EvidenceItem, EvidenceSnapshot


class GitInventoryError(RuntimeError):
    """Raised when committed Git evidence cannot be read."""


class _InvalidDefinition(ValueError):
    """Raised when one committed definition cannot be interpreted."""


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

    def deprovision_sentinels(self) -> list[str]:
        return _deprovision_sentinels(self.tracked_paths)

    def kustomizations(self, cluster_path: str) -> list[str]:
        return _kustomizations(self.tracked_paths, cluster_path)

    @staticmethod
    def _git(root: Path, arguments: list[str]) -> str:
        return read_git(root, arguments)


def read_git(root: Path, arguments: list[str]) -> str:
    """Run a read-only Git command, mapping failures to GitInventoryError."""

    try:
        return _run_git(root, arguments)
    except subprocess.CalledProcessError as error:
        output = _decode(error.stderr or error.stdout or b"").strip()
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
    )
    return _decode(result.stdout)


def _decode(output: bytes | str) -> str:
    # Text mode would translate CRLF, so file content would not match Git.
    if isinstance(output, str):
        return output
    return output.decode("utf-8", errors="replace")


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


def _deprovision_sentinels(tracked_paths: frozenset[str]) -> list[str]:
    return sorted(
        tracked_path
        for tracked_path in tracked_paths
        if len(parts := tracked_path.split("/")) == 2
        and parts[0] == "deprovision"
        and parts[1] != "README.md"
    )


def _sentinel_item(
    path: str,
    *,
    command_id: UUID,
    source_version: str,
) -> EvidenceItem:
    name = Path(path).name
    archive_suffix = name.endswith(ARCHIVE_SUFFIX)
    return EvidenceItem(
        item_id=uuid5(command_id, f"{source_version}:{path}"),
        kind="deprovision_sentinel",
        data={
            "path": path,
            "cluster_name": (
                name.removesuffix(ARCHIVE_SUFFIX) if archive_suffix else name
            ),
            "archive_suffix": archive_suffix,
        },
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


def _definition_data(
    cluster_path: str,
    *,
    status: str,
    tree: _GitTree,
    has_metadata: bool = True,
) -> dict[str, Any]:
    metadata = (
        _load_mapping(tree, cluster_path, "metadata.yaml")
        if has_metadata
        else {}
    )
    cluster_file = "hive/patches/clusterdeployment.yaml"
    managed_file = "hive/patches/managedcluster.yaml"
    cluster_patches = _load_patches(tree, cluster_path, cluster_file)
    managed_patches = _load_patches(tree, cluster_path, managed_file)
    crossplane_file = "crossplane/kustomization.yaml"
    crossplane = _load_mapping(tree, cluster_path, crossplane_file)

    crossplane_template = _crossplane_template(crossplane, crossplane_file)

    return {
        "path": cluster_path,
        "status": status,
        "cluster_name": _patch_value(
            cluster_patches,
            cluster_file,
            "/metadata/name",
        ),
        "cloud_provider": metadata.get("platform"),
        "region": _patch_value(
            cluster_patches,
            cluster_file,
            "/spec/platform/aws/region",
        ),
        "tier": _patch_value(
            managed_patches,
            managed_file,
            "/metadata/labels/tier",
        ),
        "environment": _patch_value(
            managed_patches,
            managed_file,
            "/metadata/labels/environment",
        ),
        "aws_credentials_mode": metadata.get("aws-credentials-mode"),
        "crossplane_template": crossplane_template,
        "kustomizations": tree.kustomizations(cluster_path),
        "install_image_set": _patch_value(
            cluster_patches,
            cluster_file,
            "/spec/provisioning/imageSetRef/name",
        ),
        "patch_platform": _patch_platform(cluster_patches),
        "metadata_text": (
            tree.file(f"{cluster_path}/metadata.yaml")
            if has_metadata
            else None
        ),
    }


def _crossplane_template(crossplane: dict[str, Any], name: str) -> str:
    resources = crossplane.get("resources", [])
    if not isinstance(resources, list) or not all(
        isinstance(resource, str) for resource in resources
    ):
        raise _InvalidDefinition(
            f"{name}: expected resources to be a list of paths"
        )
    return Path(resources[0]).name if resources else ""


def _patch_platform(patches: list[dict[str, Any]]) -> str | None:
    """Return the cloud named by the first /spec/platform/<cloud> patch."""

    for patch in patches:
        parts = str(patch.get("path", "")).split("/")
        if len(parts) > 3 and parts[1:3] == ["spec", "platform"]:
            return parts[3] or None
    return None


def _incomplete_sources(
    tree: _GitTree,
    cluster_path: str,
    missing_files: list[str],
) -> dict[str, str | None]:
    """Read what the present files of an incomplete definition declare."""

    sources: dict[str, str | None] = {
        "patch_platform": None,
        "crossplane_template": None,
        "tier": None,
        "remaining_files_error": None,
    }
    if missing_files == ["metadata.yaml"]:
        try:
            _definition_data(
                cluster_path,
                status="",
                tree=tree,
                has_metadata=False,
            )
        except _InvalidDefinition as error:
            sources["remaining_files_error"] = str(error)
    cluster_file = "hive/patches/clusterdeployment.yaml"
    managed_file = "hive/patches/managedcluster.yaml"
    crossplane_file = "crossplane/kustomization.yaml"
    try:
        if managed_file not in missing_files:
            sources["tier"] = _patch_value(
                _load_patches(tree, cluster_path, managed_file),
                managed_file,
                "/metadata/labels/tier",
            )
    except _InvalidDefinition:
        pass
    try:
        if cluster_file not in missing_files:
            sources["patch_platform"] = _patch_platform(
                _load_patches(tree, cluster_path, cluster_file)
            )
    except _InvalidDefinition:
        pass
    try:
        if crossplane_file not in missing_files:
            sources["crossplane_template"] = _crossplane_template(
                _load_mapping(tree, cluster_path, crossplane_file),
                crossplane_file,
            ) or None
    except _InvalidDefinition:
        pass
    return sources


def _load_yaml(tree: _GitTree, cluster_path: str, name: str) -> Any:
    try:
        return yaml.safe_load(tree.file(f"{cluster_path}/{name}"))
    except yaml.YAMLError as error:
        mark = getattr(error, "problem_mark", None)
        location = f" at line {mark.line + 1}" if mark is not None else ""
        raise _InvalidDefinition(
            f"{name}: invalid YAML{location}"
        ) from error


def _load_mapping(
    tree: _GitTree,
    cluster_path: str,
    name: str,
) -> dict[str, Any]:
    document = _load_yaml(tree, cluster_path, name)
    if not isinstance(document, dict):
        raise _InvalidDefinition(f"{name}: expected a mapping")
    return document


def _load_patches(
    tree: _GitTree,
    cluster_path: str,
    name: str,
) -> list[dict[str, Any]]:
    document = _load_yaml(tree, cluster_path, name)
    if not isinstance(document, list) or not all(
        isinstance(patch, dict) for patch in document
    ):
        raise _InvalidDefinition(
            f"{name}: expected a list of patch operations"
        )
    return document


def _patch_value(
    patches: list[dict[str, Any]],
    name: str,
    json_path: str,
) -> Any:
    for patch in patches:
        if patch.get("path") == json_path:
            return patch.get("value")
    raise _InvalidDefinition(f"{name}: missing patch path {json_path}")


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
        items.extend(
            _sentinel_item(
                path,
                command_id=command_id,
                source_version=resolved_source_version,
            )
            for path in tree.deprovision_sentinels()
        )

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
                    "cluster_name": Path(relative_path).name,
                    "missing_files": missing_files,
                    **_incomplete_sources(tree, cluster_path, missing_files),
                },
            )

        item_id = uuid5(command_id, f"{source_version}:{relative_path}")
        try:
            data = _definition_data(
                cluster_path,
                status=status,
                tree=tree,
            )
        except _InvalidDefinition as error:
            return EvidenceItem(
                item_id=item_id,
                kind="cluster_definition_invalid",
                data={
                    "path": relative_path,
                    "status": status,
                    "cluster_name": Path(relative_path).name,
                    "error": str(error),
                },
            )

        return EvidenceItem(
            item_id=item_id,
            kind="cluster_definition",
            data=data,
        )

    def _git_revision(self, tree: _GitTree) -> str:
        return tree.revision


__all__ = ["GitInventoryCollector", "GitInventoryError", "read_git"]

