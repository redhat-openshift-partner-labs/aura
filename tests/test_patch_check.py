"""Every Git proposal generator checks that its patch applies (task 4.3)."""

from __future__ import annotations

from pathlib import Path

import pytest

import aura.cli as cli
from aura.proposals import deprovision_sentinel_patch
from aura.scaffold_runner import GitPatchCheck
from aura.services.cluster_edit import ClusterEditProposalGenerator
from aura.services.proposal import (
    DeprovisionProposalGenerator,
    ProposalRefused,
)
from failure_injection import run_cli
from test_cluster_edit_proposal import evidence as edit_evidence
from test_cluster_edit_proposal import fleet as edit_fleet
from test_cluster_state_diagnosis import make_cwa_snapshot
from test_deprovision_proposal import CREATED_AT, TRACE_ID, cluster_state
from test_failure_matrix import COMMAND_ID, cluster_edit, propose
from test_git_inventory_collector import commit_repository, write_cluster
from test_proposals import added_file


class RecordingCheck:
    def __init__(self, *reasons: str) -> None:
        self.calls: list[dict[str, object]] = []
        self._reasons = list(reasons)

    def check(self, **options: object) -> list[str]:
        self.calls.append(options)
        return list(self._reasons)


def repository(tmp_path: Path, *, metadata: bool = True) -> tuple[Path, str]:
    root = tmp_path / "fleet-clusters"
    write_cluster(root)
    if not metadata:
        (root / "provision" / "example" / "metadata.yaml").unlink()
    return root, commit_repository(root)


@pytest.mark.parametrize("require_definition", [True, False])
def test_patch_check_refuses_a_patch_that_does_not_apply(
    tmp_path: Path,
    require_definition: bool,
) -> None:
    root, base = repository(tmp_path)

    [reason] = GitPatchCheck(root).check(
        base_commit=base,
        patch=added_file("provision/example/metadata.yaml"),
        cluster_name="example",
        require_definition=require_definition,
    )

    assert reason.startswith(f"patch does not apply to base commit {base}: ")


def test_patch_check_without_a_definition_checks_only_that_it_applies(
    tmp_path: Path,
) -> None:
    root, base = repository(tmp_path, metadata=False)
    options = {
        "base_commit": base,
        "patch": deprovision_sentinel_patch("example"),
        "cluster_name": "example",
    }

    assert GitPatchCheck(root).check(**options, require_definition=False) == []
    [reason] = GitPatchCheck(root).check(**options, require_definition=True)
    assert "cluster_definition_incomplete for example" in reason


def test_deprovision_generator_refuses_what_the_patch_check_reports(
    tmp_path: Path,
) -> None:
    root, base = repository(tmp_path)
    check = RecordingCheck("patch does not apply")

    with pytest.raises(ProposalRefused) as refusal:
        DeprovisionProposalGenerator(patch_check=check).generate(
            make_cwa_snapshot(cluster_state(root)),
            cwa_trace_id=TRACE_ID,
            created_at=CREATED_AT,
        )

    assert refusal.value.reasons == ["patch does not apply"]
    assert check.calls == [
        {
            "base_commit": base,
            "patch": deprovision_sentinel_patch("example"),
            "cluster_name": "example",
            "require_definition": False,
        }
    ]


def test_cluster_edit_generator_refuses_what_the_patch_check_reports(
    tmp_path: Path,
) -> None:
    root = edit_fleet(tmp_path, "htpasswd-provider-name: Idp\n")
    check = RecordingCheck("applying the patch yields no definition")
    generator = ClusterEditProposalGenerator(patch_check=check)

    with pytest.raises(ProposalRefused) as refusal:
        generator.generate(
            make_cwa_snapshot(edit_evidence(root)),
            cwa_trace_id=TRACE_ID,
            created_at=CREATED_AT,
        )

    assert refusal.value.reasons == ["applying the patch yields no definition"]
    [call] = check.calls
    assert call["require_definition"] is True
    assert call["cluster_name"] == "example"
    assert "provision/example/metadata.yaml" in str(call["patch"])


@pytest.mark.parametrize(
    "build",
    [propose(None), cluster_edit("htpasswd-provider-name: Idp\n")],
    ids=["deprovision", "cluster-edit"],
)
def test_cli_wires_the_patch_check_to_the_fleet_clusters_checkout(
    tmp_path: Path,
    monkeypatch,
    build,
) -> None:
    checks: list[RecordingCheck] = []

    def failing_check(fleet_clusters: Path) -> RecordingCheck:
        assert fleet_clusters == tmp_path / "fleet-clusters"
        checks.append(RecordingCheck("patch does not apply"))
        return checks[-1]

    monkeypatch.setattr(cli, "GitPatchCheck", failing_check)
    arguments, collector = build(tmp_path)

    run = run_cli(
        arguments,
        monkeypatch,
        collector=collector,
        command_id=COMMAND_ID,
        clock=lambda: CREATED_AT,
    )

    assert run.exit_code == 2
    assert run.stderr == "aura: proposal refused: patch does not apply\n"
    assert len(checks) == 1 and len(checks[0].calls) == 1
