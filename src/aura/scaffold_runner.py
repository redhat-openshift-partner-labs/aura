"""Run the pinned fleet-cluster-scaffold and check its patch, read-only.

Both checkouts are only read: exports come from `git ls-tree` and
`git cat-file`, and the definition check uses a `--shared --no-checkout`
clone. Every Git call drops inherited GIT_ variables and runs no hook or
fsmonitor, and every write happens in a temporary directory that is
removed afterwards.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import sys
import tempfile
from pathlib import Path
from typing import final
from uuid import UUID, uuid5

from aura.collectors.git_inventory import GitInventoryCollector, GitInventoryError
from aura.contracts import EvidenceItem
from aura.git_isolation import (
    GIT_ISOLATION,
    GIT_TIMEOUT,
    isolated_git_environment,
)
from aura.process import ProcessError, ProcessResult, run_process
from aura.scaffold import ScaffoldResult, ScaffoldRunError


_BOOTSTRAP = (
    "import sys; sys.path.insert(0, sys.argv[1]); "
    "from scaffold.cli import main; sys.exit(main(sys.argv[2:]))"
)
_CHECK_NAMESPACE = UUID("8b0f6a2e-2f53-4f1e-9a52-2b8f5c7f9d10")
_DEFINITION_KINDS = frozenset(
    {
        "cluster_definition",
        "cluster_definition_incomplete",
        "cluster_definition_invalid",
    }
)
_CHECK_IDENTITY = {
    "GIT_AUTHOR_NAME": "Aura",
    "GIT_AUTHOR_EMAIL": "aura@localhost",
    "GIT_COMMITTER_NAME": "Aura",
    "GIT_COMMITTER_EMAIL": "aura@localhost",
}


@final
class SubprocessScaffoldRunner:
    """Runs an export of the scaffold in a temporary workspace holding an
    export of fleet-clusters."""

    def __init__(
        self,
        *,
        fleet_clusters: Path,
        scaffold: Path,
        python: str = sys.executable,
        timeout: float = 300,
        temp_root: Path | None = None,
    ) -> None:
        self._fleet_clusters = fleet_clusters
        self._scaffold = scaffold
        self._python = python
        self._timeout = timeout
        self._temp_root = temp_root

    def run(
        self,
        *,
        scaffold_commit: str,
        base_commit: str,
        arguments: list[str],
    ) -> ScaffoldResult:
        with tempfile.TemporaryDirectory(dir=self._temp_root) as temp:
            root = Path(temp)
            tool = root / "scaffold"
            workspace = root / "workspace"
            _export(self._scaffold, scaffold_commit, tool)
            _export(self._fleet_clusters, base_commit, workspace)
            before = _entries(workspace)
            try:
                completed = run_process(
                    [
                        self._python,
                        "-I",
                        "-B",
                        "-c",
                        _BOOTSTRAP,
                        str(tool / "src"),
                        *arguments,
                    ],
                    cwd=workspace,
                    env={
                        "PATH": os.environ.get("PATH", os.defpath),
                        "HOME": str(root),
                        "LANG": "C.UTF-8",
                    },
                    timeout=self._timeout,
                )
            except ProcessError as error:
                raise ScaffoldRunError(
                    f"scaffold timed out after {self._timeout:g} seconds"
                    if error.timed_out
                    else f"cannot run scaffold with {self._python}: "
                    f"{error.__cause__}"
                ) from error
            after = _entries(workspace)
            added = sorted(
                path
                for path in set(after) - set(before)
                if after[path][0] != "special"
            )
            return ScaffoldResult(
                exit_code=completed.returncode,
                stdout=completed.stdout_text(),
                stderr=completed.stderr_text(),
                files={path: _read(workspace, path) for path in added},
                changed=_changes(before, after),
            )


@final
class GitDefinitionCheck:
    """Applies a patch to a shared clone and collects the definition."""

    def __init__(
        self,
        fleet_clusters: Path,
        *,
        temp_root: Path | None = None,
    ) -> None:
        self._fleet_clusters = fleet_clusters
        self._temp_root = temp_root

    def check(
        self,
        *,
        base_commit: str,
        patch: str,
        cluster_name: str,
    ) -> list[str]:
        with tempfile.TemporaryDirectory(dir=self._temp_root) as temp:
            clone = Path(temp) / "fleet-clusters"
            _git(
                None,
                "clone",
                "--quiet",
                "--shared",
                "--no-checkout",
                str(self._fleet_clusters),
                str(clone),
            )
            _git(clone, "read-tree", base_commit)
            applied = _run_git(
                clone, ["apply", "--cached", "-"], input=patch.encode()
            )
            if applied.returncode != 0:
                lines = applied.stderr.decode("utf-8", "replace").splitlines()
                return [
                    f"patch does not apply to base commit {base_commit}: "
                    f"{lines[-1].strip() if lines else 'git apply failed'}"
                ]
            tree = _git(clone, "write-tree").strip()
            commit = _git(
                clone,
                "commit-tree",
                "--no-gpg-sign",
                tree,
                "-p",
                base_commit,
                "-m",
                "Aura scaffold definition check",
                identity=True,
            ).strip()
            _git(clone, "update-ref", "HEAD", commit)
            try:
                snapshot = GitInventoryCollector(clone).collect(
                    command_id=uuid5(_CHECK_NAMESPACE, commit)
                )
            except GitInventoryError as error:
                raise ScaffoldRunError(str(error)) from error
        return _definition_reasons(
            [
                item
                for item in snapshot.items
                if item.kind in _DEFINITION_KINDS
                and item.data.get("cluster_name") == cluster_name
            ],
            base_commit,
            cluster_name,
        )


def kustomize_version() -> str | None:
    """Return `kustomize version` output, or None when it is not on PATH."""

    binary = shutil.which("kustomize")
    if binary is None:
        return None
    try:
        completed = run_process([binary, "version"], env=None, timeout=30)
    except ProcessError as error:
        raise ScaffoldRunError(f"kustomize version failed: {error}") from error
    if completed.returncode != 0:
        raise ScaffoldRunError(
            f"kustomize version failed: exit {completed.returncode}"
        )
    return completed.stdout_text().strip() or None


def _definition_reasons(
    items: list[EvidenceItem],
    base_commit: str,
    cluster: str,
) -> list[str]:
    prefix = f"applying the patch to {base_commit} yields"
    if not items:
        return [f"{prefix} no definition for {cluster}"]
    if len(items) > 1:
        return [f"{prefix} {len(items)} definitions for {cluster}"]
    [item] = items
    if item.kind != "cluster_definition":
        return [
            f"{prefix} {item.kind} for {cluster}, not a complete "
            "cluster_definition"
        ]
    if item.data.get("path") != f"provision/{cluster}":
        return [
            f"{prefix} a definition at {item.data.get('path')}, not "
            f"provision/{cluster}"
        ]
    return []


def _export(repository: Path, commit: str, target: Path) -> None:
    """Write one commit's tree byte for byte, without export attributes."""

    def fail(detail: str) -> ScaffoldRunError:
        return ScaffoldRunError(
            f"cannot export {commit} from {repository}: {detail}"
        )

    try:
        listing = _git_bytes(
            repository, "ls-tree", "-r", "-z", "--full-tree", commit
        )
    except ScaffoldRunError as error:
        raise fail(str(error)) from error
    entries = []
    for record in filter(None, listing.split(b"\0")):
        meta, raw_path = record.split(b"\t", 1)
        mode, kind, sha = meta.decode().split()
        path = raw_path.decode("utf-8", "surrogateescape")
        if kind != "blob":
            raise fail(f"{path} is a {kind}")
        entries.append((mode, sha, path))
    blobs = _git_bytes(
        repository,
        "cat-file",
        "--batch",
        input="".join(f"{sha}\n" for _, sha, _ in entries).encode(),
    )
    target.mkdir()
    root = target.resolve()
    offset = 0
    for mode, _, path in entries:
        newline = blobs.index(b"\n", offset)
        size = int(blobs[offset:newline].split()[2])
        content = blobs[newline + 1 : newline + 1 + size]
        offset = newline + 2 + size
        destination = target / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        if mode == "120000":
            link = content.decode("utf-8", "surrogateescape")
            if not (destination.parent / link).resolve().is_relative_to(root):
                raise fail(f"symlink {path} points outside the export")
            os.symlink(link, destination)
        else:
            destination.write_bytes(content)
            if mode == "100755":
                destination.chmod(0o755)


def _entries(root: Path) -> dict[str, tuple[str, ...]]:
    """Describe every entry under root, keyed by relative POSIX path."""

    found: dict[str, tuple[str, ...]] = {}
    for directory, subdirectories, names in os.walk(root):
        for name in [*names, *subdirectories]:
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode):
                found[relative] = ("link", os.readlink(path))
            elif stat.S_ISREG(info.st_mode):
                found[relative] = (
                    "file",
                    oct(info.st_mode & 0o111),
                    hashlib.sha256(path.read_bytes()).hexdigest(),
                )
            elif not stat.S_ISDIR(info.st_mode):
                found[relative] = ("special",)
    return found


def _changes(
    before: dict[str, tuple[str, ...]],
    after: dict[str, tuple[str, ...]],
) -> tuple[str, ...]:
    """Name every change the scaffold made other than adding a file."""

    changes = [
        *(f"modified {path}" for path in before if path in after and after[path] != before[path]),
        *(f"deleted {path}" for path in before if path not in after),
        *(f"special {path}" for path, entry in after.items() if entry[0] == "special"),
    ]
    return tuple(sorted(changes))


def _read(root: Path, relative: str) -> tuple[str, str]:
    path = root / relative
    if path.is_symlink():
        return "120000", os.readlink(path)
    mode = "100755" if path.stat().st_mode & 0o111 else "100644"
    try:
        return mode, path.read_bytes().decode("utf-8")
    except UnicodeDecodeError as error:
        raise ScaffoldRunError(
            f"scaffold output {relative} is not UTF-8"
        ) from error


def _run_git(
    root: Path | None,
    arguments: list[str],
    *,
    input: bytes | None = None,
    identity: bool = False,
) -> ProcessResult:
    command = [
        "git",
        *GIT_ISOLATION,
        *(["-C", str(root)] if root else []),
        *arguments,
    ]
    environment = isolated_git_environment()
    if identity:
        environment.update(_CHECK_IDENTITY)
    try:
        return run_process(
            command,
            env=environment,
            timeout=GIT_TIMEOUT,
            input=input,
        )
    except ProcessError as error:
        raise ScaffoldRunError(f"git {arguments[0]} failed: {error}") from error


def _git_bytes(
    root: Path | None,
    *arguments: str,
    input: bytes | None = None,
    identity: bool = False,
) -> bytes:
    completed = _run_git(root, list(arguments), input=input, identity=identity)
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", "replace").strip()
        raise ScaffoldRunError(
            f"git {arguments[0]} failed: "
            f"{detail.splitlines()[-1] if detail else 'no output'}"
        )
    return completed.stdout


def _git(root: Path | None, *arguments: str, identity: bool = False) -> str:
    return _git_bytes(root, *arguments, identity=identity).decode(
        "utf-8", "replace"
    )


__all__ = [
    "GIT_TIMEOUT",
    "GitDefinitionCheck",
    "SubprocessScaffoldRunner",
    "kustomize_version",
]
