from __future__ import annotations

import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
import yaml

from aura.collectors.cluster_inventory import ClusterInventoryCollector
from aura.collectors.cluster_state import ClusterStateCollector
from aura.collectors.git_inventory import GitInventoryCollector
from aura.contracts import EvidenceItem, EvidenceSnapshot
from aura.proposals import (
    SKIP_PROVISION,
    Proposal,
    ProposalType,
    Reversibility,
    patch_changes,
)
from aura.services.cluster_edit import ClusterEditProposalGenerator
from aura.services.proposal import ProposalRefused
from test_cluster_inventory_collector import (
    actual_state,
    openshift_collector,
    pipeline_run,
    tekton_collector,
)
from test_cluster_state_collector import EmptyCollector, FakeGitCollector
from test_cluster_state_diagnosis import make_cwa_snapshot
from test_git_inventory_collector import commit_repository, write_cluster


TRACE_ID = UUID(int=70)
CREATED_AT = datetime(2026, 1, 1, tzinfo=UTC)
METADATA = "provision/example/metadata.yaml"
ABSENT = object()


def fleet(
    tmp_path: Path,
    metadata: object = ABSENT,
    *,
    template: str = "crossplane",
    prepare: Callable[[Path], None] | None = None,
) -> Path:
    root = tmp_path / "fleet-clusters"
    write_cluster(root)
    if prepare is not None:
        prepare(root)
    cluster = root / "provision" / "example"
    if metadata is ABSENT:
        (cluster / "metadata.yaml").unlink()
    else:
        (cluster / "metadata.yaml").write_text(str(metadata))
    (cluster / "crossplane" / "kustomization.yaml").write_text(
        "resources:\n"
        f"- ../../../cluster-templates/aws/standard/{template}\n"
    )
    commit_repository(root)
    return root


def post_provision(**params: str) -> EvidenceItem:
    item = pipeline_run("example")
    return item.model_copy(
        update={
            "data": {
                **item.data,
                "pipeline_run_name": "post-provision-example-abc",
                "pipeline_name": "post-provision",
                "params": {"cluster-name": "example", **params},
            }
        }
    )


def evidence(
    root: Path,
    *,
    git: object | None = None,
    openshift: object | None = None,
    runs: tuple[EvidenceItem, ...] = (),
) -> EvidenceSnapshot:
    return ClusterStateCollector(
        git_collector=git or GitInventoryCollector(root),
        openshift_collector=openshift
        or openshift_collector(actual_state("example")),
        tekton_collector=tekton_collector(*runs),
        cluster_name="example",
        pipeline_name="post-provision",
    ).collect(
        command_id=UUID(int=1),
        snapshot_id=UUID(int=40),
        collected_at=CREATED_AT,
    )


def generate(snapshot: EvidenceSnapshot) -> Proposal:
    return ClusterEditProposalGenerator().generate(
        make_cwa_snapshot(snapshot),
        cwa_trace_id=TRACE_ID,
        created_at=CREATED_AT,
    )


def apply(root: Path, proposal: Proposal) -> str:
    subprocess.run(
        ["git", "-C", str(root), "apply", "--check", "-"],
        input=proposal.patch,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "-C", str(root), "apply", "-"],
        input=proposal.patch,
        check=True,
        capture_output=True,
        text=True,
    )
    commit_repository(root)
    return (root / METADATA).read_text()


def test_generator_backfills_missing_metadata_file(tmp_path: Path) -> None:
    root = fleet(tmp_path)
    snapshot = evidence(
        root,
        runs=(post_provision(**{"htpasswd-provider-name": "PartnerIDP"}),),
    )

    proposal = generate(snapshot)

    assert proposal.proposal_type is ProposalType.CLUSTER_EDIT
    assert proposal.trigger_effect == []
    assert proposal.reversibility is Reversibility.REVERSIBLE
    assert patch_changes(proposal.patch or "") == [(METADATA, "added")]
    assert SKIP_PROVISION in (proposal.commit_message or "")
    assert "PipelineRun post-provision-example-abc" in proposal.summary
    assert "fleet 9464f0b default" in proposal.summary
    assert any("head commit" in risk for risk in proposal.risks)
    assert generate(snapshot) == proposal
    assert Proposal.model_validate_json(proposal.model_dump_json()) == proposal
    assert yaml.safe_load(apply(root, proposal)) == {
        "platform": "aws",
        "aws-credentials-mode": "legacy",
        "htpasswd-provider-name": "PartnerIDP",
        "openshift-ai-version": "v3",
    }
    inventory = ClusterInventoryCollector(
        git_collector=GitInventoryCollector(root),
        openshift_collector=openshift_collector(actual_state("example")),
        tekton_collector=tekton_collector(),
    ).collect(command_id=UUID(int=2))
    [view] = [i for i in inventory.items if i.kind == "cluster_inventory"]
    assert view.data["drift_indicator"] not in {
        "incomplete-definition",
        "incomplete-metadata",
    }


@pytest.mark.parametrize(
    "original",
    [
        "htpasswd-provider-name: Red Hat",
        "# set by hand\nhtpasswd-provider-name: Red Hat\nextra-key: kept\n",
    ],
)
def test_generator_appends_missing_keys(tmp_path: Path, original: str) -> None:
    root = fleet(tmp_path, original)

    proposal = generate(evidence(root))

    assert patch_changes(proposal.patch or "") == [(METADATA, "modified")]
    assert SKIP_PROVISION not in (proposal.commit_message or "")
    updated = apply(root, proposal)
    prefix = original if original.endswith("\n") else original + "\n"
    assert updated.startswith(prefix)
    assert yaml.safe_load(updated) == {
        **yaml.safe_load(original),
        "platform": "aws",
        "aws-credentials-mode": "legacy",
        "openshift-ai-version": "v3",
    }
    [definition] = GitInventoryCollector(root).collect(
        command_id=UUID(int=3)
    ).items
    assert definition.kind == "cluster_definition"
    assert definition.data["cloud_provider"] == "aws"


def test_generator_prefers_run_values_over_defaults(tmp_path: Path) -> None:
    root = fleet(tmp_path, "platform: aws\n")
    run = post_provision(
        **{"htpasswd-provider-name": "Partner", "openshift-ai-version": "v2"}
    )

    updated = apply(root, generate(evidence(root, runs=(run,))))

    assert yaml.safe_load(updated)["openshift-ai-version"] == "v2"
    assert yaml.safe_load(updated)["htpasswd-provider-name"] == "Partner"


def _setup(
    metadata: object = ABSENT,
    *,
    template: str = "crossplane",
    prepare: Callable[[Path], None] | None = None,
    **sources: object,
):
    def build(tmp_path: Path) -> EvidenceSnapshot:
        root = fleet(tmp_path, metadata, template=template, prepare=prepare)
        return evidence(root, **sources)

    return build


def _sentinel(root: Path) -> None:
    (root / "deprovision").mkdir(exist_ok=True)
    (root / "deprovision" / "example-archive").write_text("")


def _archive(root: Path) -> None:
    write_cluster(root, directory="archive")


def _archived_only(tmp_path: Path) -> EvidenceSnapshot:
    root = tmp_path / "fleet-clusters"
    write_cluster(root, directory="archive")
    commit_repository(root)
    return evidence(root)


def _missing_managed_cluster(tmp_path: Path) -> EvidenceSnapshot:
    root = fleet(tmp_path)
    (root / "provision/example/hive/patches/managedcluster.yaml").unlink()
    commit_repository(root)
    return evidence(root)


def _openshift_azure(tmp_path: Path) -> EvidenceSnapshot:
    state = actual_state("example")
    state = state.model_copy(
        update={"data": {**state.data, "cloud_provider": "azure"}}
    )
    return evidence(fleet(tmp_path), openshift=openshift_collector(state))


FULL = (
    "platform: aws\naws-credentials-mode: legacy\n"
    "htpasswd-provider-name: htpasswd\nopenshift-ai-version: v3\n"
)
RUN = "PipelineRun post-provision-example-abc"


@pytest.mark.parametrize(
    ("build", "reason"),
    [
        (
            _setup(FULL),
            "metadata.yaml for example already declares every key",
        ),
        (
            _setup(prepare=_sentinel),
            "deprovision sentinel already exists: deprovision/example-archive",
        ),
        (
            _setup(openshift=EmptyCollector()),
            "no OpenShift resources for cluster example",
        ),
        (
            _archived_only,
            "cluster example is defined at archive/example, "
            "not provision/example",
        ),
        (
            _setup(prepare=_archive),
            "conflicting Git cluster definitions for example",
        ),
        (
            _missing_managed_cluster,
            "definition for example is missing files other than "
            "metadata.yaml: ['hive/patches/managedcluster.yaml']",
        ),
        (
            _setup("- aws\n"),
            "definition for example is invalid: "
            "metadata.yaml: expected a mapping",
        ),
        (
            _openshift_azure,
            "platform evidence conflicts for example: "
            "ClusterDeployment patch aws, OpenShift azure",
        ),
        (
            _setup("platform: azure\n"),
            "metadata.yaml declares platform azure but evidence shows aws",
        ),
        (
            _setup(template="crossplane-scoped"),
            "cluster example uses scoped AWS credentials but metadata.yaml "
            "does not declare aws-credentials-mode",
        ),
        (
            _setup(
                "aws-credentials-mode: legacy\n",
                template="crossplane-scoped",
            ),
            "metadata.yaml declares aws-credentials-mode legacy but the "
            "Crossplane template is crossplane-scoped",
        ),
        (
            _setup("aws-credentials-mode: scoped\n"),
            "metadata.yaml declares aws-credentials-mode scoped but the "
            "Crossplane template is crossplane",
        ),
        (
            _setup(
                "htpasswd-provider-name: A\n",
                runs=(post_provision(**{"htpasswd-provider-name": "B"}),),
            ),
            f"metadata.yaml declares htpasswd-provider-name A but {RUN} "
            "used B",
        ),
        (
            _setup(
                runs=(
                    post_provision(
                        **{"htpasswd-provider-name": "[REDACTED]"}
                    ),
                ),
            ),
            f"{RUN} parameter htpasswd-provider-name is redacted",
        ),
        (
            _setup(git=FakeGitCollector()),
            "no pinned Git base commit in evidence",
        ),
        (
            _setup("{htpasswd-provider-name: x}\n"),
            "metadata.yaml for example cannot be extended by appending keys",
        ),
    ],
)
def test_generator_refuses_unmet_preconditions(
    tmp_path: Path,
    build: Callable[[Path], EvidenceSnapshot],
    reason: str,
) -> None:
    with pytest.raises(ProposalRefused) as refusal:
        generate(build(tmp_path))

    assert reason in refusal.value.reasons
