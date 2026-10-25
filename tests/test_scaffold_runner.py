from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from aura.proposals import added_file_patch
from aura.collectors.scaffold_evidence import ScaffoldEvidenceCollector
from aura.contracts import EvidenceSnapshot
from aura.scaffold import ScaffoldRequest, ScaffoldRunError
from aura.scaffold_runner import (
    GitDefinitionCheck,
    SubprocessScaffoldRunner,
    kustomize_version,
)
from fake_scaffold import fake_scaffold, fleet_with_templates, overlay_files
from test_git_inventory_collector import commit_repository, write_cluster


NEW = "new-cluster"


def git(root: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def head(root: Path) -> str:
    return git(root, "rev-parse", "HEAD").strip()


def arguments(cluster: str = NEW, *options: str) -> list[str]:
    return [
        "aws",
        "--name",
        cluster,
        "--region",
        "us-east-1",
        "--tier",
        "base",
        *options,
        "--base-path",
        "provision",
        "--cluster-templates-dir",
        "cluster-templates",
    ]


def checkout_state(root: Path) -> tuple[str, str, str]:
    """Everything a run could change in a checkout."""

    return (
        git(root, "status", "--porcelain", "--ignored"),
        git(root, "for-each-ref"),
        git(root, "count-objects", "-v"),
    )


@pytest.fixture
def trees(tmp_path: Path) -> dict[str, object]:
    fleet = fleet_with_templates(tmp_path / "fleet-clusters", "base")
    base = head(fleet)
    (fleet / "cluster-templates" / "marker.txt").write_text("head\n")
    commit_repository(fleet)
    scaffold = fake_scaffold(tmp_path / "fleet-cluster-scaffold")
    return {
        "fleet": fleet,
        "base": base,
        "scaffold": scaffold,
        "commit": head(scaffold),
        "temp": tmp_path / "tmp",
    }


def runner(trees: dict[str, object], **options: object):
    temp = trees["temp"]
    assert isinstance(temp, Path)
    temp.mkdir(exist_ok=True)
    return SubprocessScaffoldRunner(
        fleet_clusters=trees["fleet"],
        scaffold=trees["scaffold"],
        temp_root=temp,
        **options,
    )


def run(trees: dict[str, object], *options: str, **settings: object):
    return runner(trees, **settings).run(
        scaffold_commit=str(trees["commit"]),
        base_commit=str(trees["base"]),
        arguments=arguments(NEW, *options),
    )


# Runner behavior


def test_runner_returns_the_files_the_scaffold_adds(trees) -> None:
    result = run(trees)

    assert result.exit_code == 0
    assert result.stdout == ""
    assert f"Cluster '{NEW}' scaffolded successfully" in result.stderr
    assert result.files == {
        path: ("100644", text)
        for path, text in overlay_files(arguments()).items()
    }


def test_runner_runs_the_pinned_commit_not_the_working_tree(trees) -> None:
    cli = Path(str(trees["scaffold"])) / "src" / "scaffold" / "cli.py"
    cli.write_text("raise SystemExit('working tree used')\n")

    assert run(trees).exit_code == 0


def test_runner_workspace_holds_the_base_commit(trees) -> None:
    result = run(trees, "--environment", "template")

    assert result.files[f"provision/{NEW}/marker.yaml"] == (
        "100644",
        "marker: base\n",
    )


def test_runner_reports_symlinks_and_executables(trees) -> None:
    linked = run(trees, "--environment", "symlink").files
    executable = run(trees, "--environment", "exec").files

    assert linked[f"provision/{NEW}/link.yaml"] == ("120000", "metadata.yaml")
    assert executable[f"provision/{NEW}/run.sh"] == ("100755", "#!/bin/sh\n")


def test_runner_captures_a_failed_run(trees) -> None:
    result = run(trees, "--environment", "fail")

    assert result.exit_code == 1
    assert "bad --environment value" in result.stderr
    assert result.files == {}


def test_runner_passes_a_minimal_environment(trees, monkeypatch) -> None:
    monkeypatch.setenv("KUBECONFIG", "/home/operator/.kube/admin")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")

    names = json.loads(run(trees, "--environment", "env").stdout)

    assert "KUBECONFIG" not in names
    assert "AWS_SECRET_ACCESS_KEY" not in names
    # macOS may add __CF_USER_TEXT_ENCODING to any process.
    assert set(names) <= {
        "PATH",
        "HOME",
        "LANG",
        "LC_CTYPE",
        "PWD",
        "__CF_USER_TEXT_ENCODING",
    }


def test_runner_keeps_option_values_with_spaces(trees) -> None:
    result = run(trees, "--htpasswd-provider-name", "Acme Corp")

    assert result.files[f"provision/{NEW}/metadata.yaml"][1].endswith(
        "htpasswd-provider-name: Acme Corp\n"
    )


def test_runner_changes_neither_checkout(trees) -> None:
    fleet, scaffold = Path(str(trees["fleet"])), Path(str(trees["scaffold"]))
    before = (checkout_state(fleet), checkout_state(scaffold))

    run(trees)

    assert (checkout_state(fleet), checkout_state(scaffold)) == before


@pytest.mark.parametrize("environment", ["development", "fail"])
def test_runner_removes_its_workspace(trees, environment: str) -> None:
    run(trees, "--environment", environment)

    assert list(Path(str(trees["temp"])).iterdir()) == []


def test_runner_replaces_undecodable_stream_bytes(trees) -> None:
    result = run(trees, "--environment", "badstdout")

    assert result.exit_code == 0
    assert "\ufffd" in result.stdout


def test_runner_gives_the_scaffold_no_input(trees) -> None:
    script = (
        "import sys, json\n"
        "from pathlib import Path\n"
        "from aura.scaffold_runner import SubprocessScaffoldRunner\n"
        "fleet, scaffold, base, commit, temp = sys.argv[1:]\n"
        "result = SubprocessScaffoldRunner(fleet_clusters=Path(fleet), "
        "scaffold=Path(scaffold), temp_root=Path(temp), timeout=20).run(\n"
        "    scaffold_commit=commit, base_commit=base,\n"
        f"    arguments={arguments(NEW, '--environment', 'stdin')!r})\n"
        "print(json.dumps(result.stdout))\n"
    )
    temp = Path(str(trees["temp"]))
    temp.mkdir()

    child = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(trees["fleet"]),
            str(trees["scaffold"]),
            str(trees["base"]),
            str(trees["commit"]),
            str(temp),
        ],
        input="operator-terminal-input",
        capture_output=True,
        text=True,
        check=True,
    )

    assert json.loads(child.stdout) == "\n"


@pytest.mark.parametrize(
    ("environment", "change"),
    [
        ("modify", "modified cluster-templates/marker.txt"),
        ("delete", "deleted provision/existing/metadata.yaml"),
        ("fifo", f"special provision/{NEW}/pipe"),
    ],
)
def test_runner_reports_changes_other_than_added_files(
    trees,
    environment: str,
    change: str,
) -> None:
    result = run(trees, "--environment", environment)

    assert change in result.changed
    assert f"provision/{NEW}/pipe" not in result.files


def test_runner_ignores_export_attributes(tmp_path: Path) -> None:
    fleet = fleet_with_templates(tmp_path / "fleet-clusters", "$Format:%H$")
    (fleet / ".gitattributes").write_text(
        "cluster-templates/marker.txt export-subst\n"
        "cluster-templates/ignored.txt export-ignore\n"
    )
    (fleet / "cluster-templates" / "ignored.txt").write_text("kept\n")
    commit_repository(fleet)
    scaffold = fake_scaffold(tmp_path / "fleet-cluster-scaffold")
    trees = {
        "fleet": fleet,
        "base": head(fleet),
        "scaffold": scaffold,
        "commit": head(scaffold),
        "temp": tmp_path / "tmp",
    }

    marker = run(trees, "--environment", "template").files
    ignored = run(trees, "--environment", "ignored").files

    assert marker[f"provision/{NEW}/marker.yaml"][1] == "marker: $Format:%H$\n"
    assert ignored[f"provision/{NEW}/ignored.yaml"][1] == "present: True\n"


def test_runner_refuses_a_symlink_that_leaves_the_export(
    tmp_path: Path,
) -> None:
    fleet = fleet_with_templates(tmp_path / "fleet-clusters")
    os.symlink("../../outside", fleet / "cluster-templates" / "escape")
    commit_repository(fleet)
    scaffold = fake_scaffold(tmp_path / "fleet-cluster-scaffold")
    trees = {
        "fleet": fleet,
        "base": head(fleet),
        "scaffold": scaffold,
        "commit": head(scaffold),
        "temp": tmp_path / "tmp",
    }

    with pytest.raises(ScaffoldRunError, match="points outside"):
        run(trees)


def isolated_git_config(tmp_path: Path, monkeypatch) -> Path:
    """Point HOME at a Git config whose hooks and fsmonitor leave a mark."""

    home = tmp_path / "home"
    hooks = home / "hooks"
    hooks.mkdir(parents=True)
    marks = tmp_path / "marks"
    for name in ("reference-transaction", "post-index-change", "fsmonitor"):
        script = hooks / name
        script.write_text(f"#!/bin/sh\necho {name} >> {marks}\nexit 0\n")
        script.chmod(0o755)
    (home / ".gitconfig").write_text(
        f"[core]\n\thooksPath = {hooks}\n\tfsmonitor = {hooks / 'fsmonitor'}\n"
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("GIT_CONFIG_GLOBAL", raising=False)
    return marks


def test_runner_and_check_run_no_git_hooks(trees, tmp_path, monkeypatch) -> None:
    marks = isolated_git_config(tmp_path, monkeypatch)

    run(trees)
    GitDefinitionCheck(Path(str(trees["fleet"]))).check(
        base_commit=str(trees["base"]),
        patch=patch_for(overlay_files(arguments())),
        cluster_name=NEW,
    )

    assert not marks.exists()


@pytest.mark.parametrize(
    "variable",
    ["GIT_DIR", "GIT_INDEX_FILE", "GIT_WORK_TREE", "GIT_OBJECT_DIRECTORY"],
)
def test_check_ignores_inherited_git_environment(
    trees,
    tmp_path,
    monkeypatch,
    variable: str,
) -> None:
    fleet = Path(str(trees["fleet"]))
    values = {
        "GIT_DIR": str(fleet / ".git"),
        "GIT_INDEX_FILE": str(tmp_path / "outside-index"),
        "GIT_WORK_TREE": str(fleet),
        "GIT_OBJECT_DIRECTORY": str(fleet / ".git" / "objects"),
    }
    index = (fleet / ".git" / "index").read_bytes()
    before = checkout_state(fleet)
    monkeypatch.setenv(variable, values[variable])

    reasons = GitDefinitionCheck(fleet).check(
        base_commit=str(trees["base"]),
        patch=patch_for(overlay_files(arguments())),
        cluster_name=NEW,
    )
    monkeypatch.delenv(variable)

    assert reasons == []
    assert checkout_state(fleet) == before
    assert (fleet / ".git" / "index").read_bytes() == index
    assert not (tmp_path / "outside-index").exists()


def test_runner_ignores_an_inherited_git_dir(trees, monkeypatch) -> None:
    monkeypatch.setenv("GIT_DIR", str(Path(str(trees["scaffold"])) / ".git"))

    result = run(trees, "--environment", "template")

    assert result.files[f"provision/{NEW}/marker.yaml"][1] == "marker: base\n"


def test_collector_does_not_rewrite_the_scaffold_index(
    trees,
    tmp_path,
) -> None:
    scaffold = Path(str(trees["scaffold"]))
    cli = scaffold / "src" / "scaffold" / "cli.py"
    os.utime(cli, (1, 1))
    index = hashlib.sha256((scaffold / ".git" / "index").read_bytes())

    ScaffoldEvidenceCollector(
        request=ScaffoldRequest(
            cloud="aws", cluster_name=NEW, region="us-east-1", tier="base"
        ),
        git_collector=_NoGit(),
        openshift_client=_NoOpenShift(),
        scaffold_checkout=scaffold,
        kustomize_version=lambda: None,
    ).collect(command_id=UUID(int=1))

    assert hashlib.sha256(
        (scaffold / ".git" / "index").read_bytes()
    ).digest() == index.digest()


class _NoGit:
    def collect(self, **_: object) -> EvidenceSnapshot:
        return EvidenceSnapshot(
            snapshot_id=UUID(int=2),
            command_id=UUID(int=1),
            collector="git_inventory",
            collector_version="test",
            collected_at=datetime(2026, 1, 1, tzinfo=UTC),
            source="fleet-clusters",
            source_version="git-version",
            items=[],
        )


class _NoOpenShift:
    def list_cluster_deployments(self) -> list[dict[str, Any]]:
        return []

    def list_managed_clusters(self) -> list[dict[str, Any]]:
        return []

    def resource_version(self) -> str:
        return "none"


# Runner failures


def test_runner_times_out(trees) -> None:
    with pytest.raises(ScaffoldRunError, match="timed out after 1 seconds"):
        run(trees, "--environment", "hang", timeout=1)

    assert list(Path(str(trees["temp"])).iterdir()) == []


@pytest.mark.parametrize("which", ["scaffold_commit", "base_commit"])
def test_runner_fails_on_a_missing_commit(trees, which: str) -> None:
    commits = {
        "scaffold_commit": str(trees["commit"]),
        "base_commit": str(trees["base"]),
        which: "0" * 40,
    }

    with pytest.raises(ScaffoldRunError, match="0000000000"):
        runner(trees).run(arguments=arguments(), **commits)


def test_runner_fails_without_an_interpreter(trees) -> None:
    with pytest.raises(ScaffoldRunError, match="cannot run"):
        run(trees, python="/nonexistent/python3")


def test_runner_fails_on_output_that_is_not_utf8(trees) -> None:
    with pytest.raises(ScaffoldRunError, match="blob.yaml is not UTF-8"):
        run(trees, "--environment", "binary")


# Definition check


def patch_for(files: dict[str, str]) -> str:
    return "".join(
        added_file_patch(path, files[path]) for path in sorted(files)
    )


def test_check_accepts_a_complete_definition(trees) -> None:
    fleet = Path(str(trees["fleet"]))
    before = checkout_state(fleet)

    reasons = GitDefinitionCheck(fleet).check(
        base_commit=str(trees["base"]),
        patch=patch_for(overlay_files(arguments())),
        cluster_name=NEW,
    )

    assert reasons == []
    assert checkout_state(fleet) == before


def test_check_uses_the_base_commit(trees) -> None:
    fleet = Path(str(trees["fleet"]))
    write_cluster(fleet, cluster_name=NEW)
    commit_repository(fleet)

    reasons = GitDefinitionCheck(fleet).check(
        base_commit=str(trees["base"]),
        patch=patch_for(overlay_files(arguments())),
        cluster_name=NEW,
    )

    assert reasons == []


def test_check_refuses_a_patch_that_does_not_apply(trees) -> None:
    base = str(trees["base"])
    files = {"provision/existing/metadata.yaml": "platform: aws\n"}

    [reason] = GitDefinitionCheck(Path(str(trees["fleet"]))).check(
        base_commit=base,
        patch=patch_for(files),
        cluster_name="existing",
    )

    assert reason.startswith(f"patch does not apply to base commit {base}: ")


def test_check_refuses_an_incomplete_definition(trees) -> None:
    base = str(trees["base"])
    files = overlay_files(arguments())
    del files[f"provision/{NEW}/hive/patches/managedcluster.yaml"]

    reasons = GitDefinitionCheck(Path(str(trees["fleet"]))).check(
        base_commit=base,
        patch=patch_for(files),
        cluster_name=NEW,
    )

    assert reasons == [
        f"applying the patch to {base} yields cluster_definition_incomplete "
        f"for {NEW}, not a complete cluster_definition"
    ]


def test_check_fails_on_a_missing_base_commit(trees) -> None:
    with pytest.raises(ScaffoldRunError, match="read-tree"):
        GitDefinitionCheck(Path(str(trees["fleet"]))).check(
            base_commit="0" * 40,
            patch=patch_for(overlay_files(arguments())),
            cluster_name=NEW,
        )


def test_check_removes_its_clone(trees) -> None:
    temp = Path(str(trees["temp"]))
    temp.mkdir()

    GitDefinitionCheck(Path(str(trees["fleet"])), temp_root=temp).check(
        base_commit=str(trees["base"]),
        patch=patch_for(overlay_files(arguments())),
        cluster_name=NEW,
    )

    assert list(temp.iterdir()) == []


# kustomize


def test_kustomize_version_reads_the_binary_on_path(
    tmp_path: Path,
    monkeypatch,
) -> None:
    binary = tmp_path / "bin" / "kustomize"
    binary.parent.mkdir()
    binary.write_text("#!/bin/sh\necho v5.8.1\n")
    binary.chmod(0o755)
    monkeypatch.setenv("PATH", str(binary.parent))

    assert kustomize_version() == "v5.8.1"


def test_kustomize_version_fails_when_the_binary_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    binary = tmp_path / "bin" / "kustomize"
    binary.parent.mkdir()
    binary.write_text("#!/bin/sh\nexit 3\n")
    binary.chmod(0o755)
    monkeypatch.setenv("PATH", str(binary.parent))

    with pytest.raises(ScaffoldRunError, match="kustomize version failed"):
        kustomize_version()


def test_kustomize_version_is_none_without_the_binary(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))

    assert kustomize_version() is None
