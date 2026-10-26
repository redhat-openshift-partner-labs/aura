"""Process start failures keep the messages each caller reported before
the process chokepoint, which changed only how programs are run."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from aura.collectors.git_inventory import GitInventoryError, read_git
from aura.collectors.git_staleness import GitProposalStaleness
from aura.collectors.oc_cli import OpenShiftCLIError
from aura.collectors.openshift_cli_client import OpenShiftCLIInventoryClient
from aura.scaffold import ScaffoldRunError
from aura.scaffold_runner import kustomize_version


@pytest.fixture
def empty_path(tmp_path: Path, monkeypatch) -> Path:
    directory = tmp_path / "empty-bin"
    directory.mkdir()
    monkeypatch.setenv("PATH", str(directory))
    return directory


def test_missing_oc_reports_the_os_error(tmp_path: Path, empty_path) -> None:
    client = OpenShiftCLIInventoryClient(kubeconfig_path=tmp_path / "config")

    with pytest.raises(OpenShiftCLIError) as error:
        client.list_cluster_deployments()

    assert str(error.value) == (
        "oc command failed for clusterdeployments: "
        "[Errno 2] No such file or directory: 'oc'"
    )


def test_missing_git_reports_an_unusable_checkout(
    tmp_path: Path,
    empty_path,
) -> None:
    with pytest.raises(GitInventoryError) as error:
        read_git(tmp_path, ["rev-parse", "HEAD"])

    assert str(error.value) == f"git checkout is not usable: {tmp_path}"


def test_missing_git_in_staleness_reports_an_unusable_checkout(
    tmp_path: Path,
    empty_path,
) -> None:
    staleness = GitProposalStaleness(tmp_path)

    with pytest.raises(GitInventoryError) as error:
        staleness._history_error("0" * 40, "1" * 40)

    assert str(error.value) == f"git checkout is not usable: {tmp_path}"


def test_failing_kustomize_reports_the_exit_status(
    tmp_path: Path,
    monkeypatch,
) -> None:
    binary = tmp_path / "bin" / "kustomize"
    binary.parent.mkdir()
    binary.write_text("#!/bin/sh\nexit 3\n")
    binary.chmod(0o755)
    monkeypatch.setenv("PATH", str(binary.parent))

    with pytest.raises(ScaffoldRunError) as error:
        kustomize_version()

    assert str(error.value) == (
        "kustomize version failed: "
        f"{subprocess.CalledProcessError(3, [str(binary), 'version'])}"
    )
