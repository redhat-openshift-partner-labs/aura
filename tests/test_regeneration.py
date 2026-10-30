"""G8 regeneration for each proposal type (Phase 3 task 3.4, E2, E4).

Unchanged evidence regenerates an equal action digest; a changed Git or
live input is refused or changes the action, which G8 refuses.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from aura.cli import RegenerationSources, regenerate_proposal
from aura.contracts import EvidenceSnapshot
from aura.execution.action import action_digest
from aura.execution.commands import Refusal
from aura.proposals import Proposal
from failure_injection import run_cli
from test_failure_matrix import COMMAND_ID, scenario_named
from test_git_inventory_collector import commit_repository


CLOCK = lambda: datetime(2026, 1, 1, tzinfo=UTC)  # noqa: E731
SCENARIOS = {
    "deprovision": "propose",
    "cluster_edit": "cluster-edit-file",
    "post_provision_rerun": "rerun",
    "scaffold": "scaffold",
}


def stored(tmp_path: Path, monkeypatch, name: str):
    arguments, collector = scenario_named(name).build(tmp_path)
    run = run_cli(arguments, monkeypatch, collector=collector, command_id=COMMAND_ID, clock=CLOCK)
    assert run.exit_code == 0, run.stderr
    (path,) = (tmp_path / "proposal").glob("*.json")
    proposal = Proposal.model_validate(json.loads(path.read_text(encoding="utf-8")))
    sources = RegenerationSources(
        fleet_clusters=tmp_path / "fleet-clusters",
        openshift_kubeconfig=tmp_path / "kubeconfig",
        scaffold=tmp_path / "fleet-cluster-scaffold",
    )
    return proposal, sources, collector


@pytest.mark.parametrize("proposal_type", sorted(SCENARIOS))
def test_unchanged_evidence_regenerates_the_same_action(tmp_path, monkeypatch, proposal_type) -> None:
    proposal, sources, collector = stored(tmp_path, monkeypatch, SCENARIOS[proposal_type])

    regenerated = regenerate_proposal(proposal, sources, clock=CLOCK, collector=collector)

    assert regenerated.proposal_type == proposal.proposal_type
    assert action_digest(regenerated) == action_digest(proposal)


def change_git(sources: RegenerationSources, proposal: Proposal) -> None:
    """Commit a change to the proposal's cluster in fleet-clusters."""

    root = sources.fleet_clusters
    definition = root / "provision" / proposal.cluster_name
    if proposal.proposal_type.value == "scaffold":
        # The new cluster's name is now taken.
        definition.mkdir(parents=True)
        (definition / "metadata.yaml").write_text("platform: aws\n", encoding="utf-8")
    elif proposal.proposal_type.value == "deprovision":
        sentinel = root / "deprovision" / f"{proposal.cluster_name}-archive"
        sentinel.parent.mkdir(exist_ok=True)
        sentinel.write_text("", encoding="utf-8")
    else:
        (definition / "metadata.yaml").write_text(
            "platform: aws\naws-credentials-mode: scoped\n"
            "htpasswd-provider-name: Other\nopenshift-ai-version: v9\n",
            encoding="utf-8",
        )
    commit_repository(root)


@pytest.mark.parametrize("proposal_type", sorted(SCENARIOS))
def test_a_changed_git_input_is_refused_or_changes_the_action(tmp_path, monkeypatch, proposal_type) -> None:
    proposal, sources, collector = stored(tmp_path, monkeypatch, SCENARIOS[proposal_type])
    change_git(sources, proposal)

    try:
        regenerated = regenerate_proposal(proposal, sources, clock=CLOCK, collector=collector)
    except Refusal:
        return
    assert action_digest(regenerated) != action_digest(proposal)


class VanishedCluster:
    """A live source in which the proposal's cluster no longer exists."""

    def __init__(self, collector) -> None:
        self._collector = collector

    def collect(self, **options):
        snapshot = self._collector.collect(**options)
        document = snapshot.model_dump(mode="json")
        document["items"] = [
            item for item in document["items"]
            if not item["kind"].startswith(("openshift", "tekton", "cluster_state"))
        ]
        document["snapshot_digest"] = ""
        return EvidenceSnapshot.model_validate(document)


@pytest.mark.parametrize("proposal_type", ["deprovision", "cluster_edit", "post_provision_rerun"])
def test_a_changed_live_input_is_refused(tmp_path, monkeypatch, proposal_type) -> None:
    proposal, sources, collector = stored(tmp_path, monkeypatch, SCENARIOS[proposal_type])

    with pytest.raises(Refusal):
        regenerate_proposal(proposal, sources, clock=CLOCK, collector=VanishedCluster(collector))
