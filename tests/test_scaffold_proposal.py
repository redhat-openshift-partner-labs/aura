from __future__ import annotations

import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
import yaml

from aura.collectors.git_inventory import GitInventoryCollector
from aura.collectors.git_staleness import GitProposalStaleness
from aura.collectors.scaffold_evidence import ScaffoldEvidenceCollector
from aura.contracts import EvidenceSnapshot
from aura.proposals import (
    SKIP_PROVISION,
    Proposal,
    ProposalType,
    Reversibility,
    TriggerEffect,
    patch_changes,
)
from aura.scaffold import (
    ScaffoldCheckoutError,
    ScaffoldRequest,
    ScaffoldResult,
    ScaffoldRunError,
    infra_prefix,
)
from aura.services.proposal import ProposalRefused
from aura.services.scaffold import ScaffoldProposalGenerator
from aura.scaffold_runner import GitDefinitionCheck
from fake_scaffold import overlay_files
from test_cluster_state_collector import FakeGitCollector
from test_cluster_state_diagnosis import assert_references, make_cwa_snapshot
from test_git_inventory_collector import commit_repository, write_cluster


TRACE_ID = UUID(int=70)
CREATED_AT = datetime(2026, 1, 1, tzinfo=UTC)
NEW = "new-cluster"
REGION = "us-east-1"
KUSTOMIZE = "v5.8.1"
REGULAR = "100644"


def overlay(arguments: list[str]) -> dict[str, tuple[str, str]]:
    """Files the fake scaffold writes for these arguments."""

    return {
        path: (REGULAR, text) for path, text in overlay_files(arguments).items()
    }


class PassingCheck:
    """Records definition checks and reports the given reasons."""

    def __init__(self, *reasons: str) -> None:
        self.calls: list[dict[str, str]] = []
        self._reasons = list(reasons)

    def check(
        self,
        *,
        base_commit: str,
        patch: str,
        cluster_name: str,
    ) -> list[str]:
        self.calls.append(
            {
                "base_commit": base_commit,
                "patch": patch,
                "cluster_name": cluster_name,
            }
        )
        return list(self._reasons)


class FakeRunner:
    """Records scaffold runs and returns a canned or edited overlay."""

    def __init__(
        self,
        edit: Callable[[dict[str, tuple[str, str]]], None] | None = None,
        *,
        exit_code: int = 0,
        changed: tuple[str, ...] = (),
        stdout: str = "",
        stderr: str = "Cluster scaffolded successfully\n",
        error: Exception | None = None,
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self._edit = edit
        self._exit_code = exit_code
        self._changed = changed
        self._stdout = stdout
        self._stderr = stderr
        self._error = error

    def run(
        self,
        *,
        scaffold_commit: str,
        base_commit: str,
        arguments: list[str],
    ) -> ScaffoldResult:
        self.calls.append(
            {
                "scaffold_commit": scaffold_commit,
                "base_commit": base_commit,
                "arguments": list(arguments),
            }
        )
        if self._error is not None:
            raise self._error
        files = overlay(arguments) if self._exit_code == 0 else {}
        if self._edit is not None:
            self._edit(files)
        return ScaffoldResult(
            exit_code=self._exit_code,
            stdout=self._stdout,
            stderr=self._stderr,
            files=files,
            changed=self._changed,
        )


class FakeOpenShiftClient:
    """Lists ClusterDeployments and ManagedClusters by name."""

    def __init__(
        self,
        deployments: tuple[str, ...],
        managed: tuple[str, ...],
    ) -> None:
        self._deployments = deployments
        self._managed = managed

    def list_cluster_deployments(self) -> list[dict[str, Any]]:
        return [
            {"metadata": {"name": name, "namespace": name}}
            for name in self._deployments
        ]

    def list_managed_clusters(self) -> list[dict[str, Any]]:
        return [{"metadata": {"name": name}} for name in self._managed]

    def resource_version(self) -> str:
        return "openshift-version"


def head(root: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def fleet(
    tmp_path: Path,
    *,
    names: tuple[str, ...] = ("existing",),
    archived: tuple[str, ...] = (),
    sentinels: tuple[str, ...] = (),
) -> Path:
    root = tmp_path / "fleet-clusters"
    for name in names:
        write_cluster(root, cluster_name=name)
    for name in archived:
        write_cluster(root, directory="archive", cluster_name=name)
    for name in sentinels:
        (root / "deprovision").mkdir(exist_ok=True)
        (root / "deprovision" / name).write_text("")
    commit_repository(root)
    return root


def scaffold_checkout(tmp_path: Path, *, dirty: bool = False) -> Path:
    root = tmp_path / "fleet-cluster-scaffold"
    (root / "src").mkdir(parents=True)
    (root / "src" / "cli.py").write_text("print('scaffold')\n")
    commit_repository(root)
    if dirty:
        (root / "src" / "cli.py").write_text("print('changed')\n")
    return root


def request(
    cluster: str = NEW,
    *options: tuple[str, str],
    cloud: str = "aws",
    tier: str = "base",
    region: str = REGION,
) -> ScaffoldRequest:
    return ScaffoldRequest(
        cloud=cloud,
        cluster_name=cluster,
        region=region,
        tier=tier,
        options=options,
    )


def evidence(
    tmp_path: Path,
    scaffold_request: ScaffoldRequest | None = None,
    *,
    root: Path | None = None,
    git: object | None = None,
    openshift: tuple[str, ...] = ("existing",),
    managed: tuple[str, ...] = ("existing",),
    dirty: bool = False,
    kustomize: str | None = KUSTOMIZE,
    checkout: Path | None = None,
) -> EvidenceSnapshot:
    root = root or fleet(tmp_path)
    return ScaffoldEvidenceCollector(
        request=scaffold_request or request(),
        git_collector=git or GitInventoryCollector(root),
        openshift_client=FakeOpenShiftClient(openshift, managed),
        scaffold_checkout=checkout or scaffold_checkout(tmp_path, dirty=dirty),
        kustomize_version=lambda: kustomize,
    ).collect(
        command_id=UUID(int=1),
        snapshot_id=UUID(int=40),
        collected_at=CREATED_AT,
    )


def generate(
    snapshot: EvidenceSnapshot,
    runner: FakeRunner,
    definition_check: object | None = None,
) -> Proposal:
    return ScaffoldProposalGenerator(
        runner=runner,
        definition_check=definition_check or PassingCheck(),
    ).generate(
        make_cwa_snapshot(snapshot),
        cwa_trace_id=TRACE_ID,
        created_at=CREATED_AT,
    )


def expected_arguments(
    cluster: str = NEW,
    *options: str,
    tier: str = "base",
) -> list[str]:
    return [
        "aws",
        "--name",
        cluster,
        "--region",
        REGION,
        "--tier",
        tier,
        *options,
        "--base-path",
        "provision",
        "--cluster-templates-dir",
        "cluster-templates",
    ]


def apply(root: Path, proposal: Proposal) -> None:
    for arguments in (["apply", "--check", "-"], ["apply", "-"]):
        subprocess.run(
            ["git", "-C", str(root), *arguments],
            input=proposal.patch,
            check=True,
            capture_output=True,
            text=True,
        )
    commit_repository(root)


# Behavior


def test_generator_proposes_the_scaffold_output(tmp_path: Path) -> None:
    root = fleet(tmp_path)
    snapshot = evidence(tmp_path, root=root)
    runner = FakeRunner()

    proposal = generate(snapshot, runner)

    assert_references(proposal, snapshot, TRACE_ID)
    scaffold_commit = head(tmp_path / "fleet-cluster-scaffold")
    arguments = expected_arguments()
    assert runner.calls == [
        {
            "scaffold_commit": scaffold_commit,
            "base_commit": head(root),
            "arguments": arguments,
        }
    ]
    assert proposal.proposal_type is ProposalType.SCAFFOLD
    assert proposal.cluster_name == NEW
    assert proposal.base_commit == head(root)
    assert proposal.scaffold_commit == scaffold_commit
    assert proposal.scaffold_run is not None
    assert proposal.scaffold_run.arguments == arguments
    assert proposal.scaffold_run.kustomize_version == KUSTOMIZE
    assert proposal.scaffold_run.exit_code == 0
    assert proposal.scaffold_run.stderr == "Cluster scaffolded successfully\n"
    assert proposal.scaffold_run.files == sorted(overlay(arguments))
    assert scaffold_commit in proposal.summary
    assert Proposal.model_validate_json(proposal.model_dump_json()) == proposal


@pytest.mark.parametrize(
    "options",
    [
        (("--topology", "compact"),),
        (("--topology", "sno"), ("--htpasswd-provider-name", "Acme Corp")),
        (
            ("--environment", "production"),
            ("--image-set", "img4.21.24-x86-64-appsub"),
            ("--zones", "us-east-1a,us-east-1b"),
        ),
    ],
)
def test_generator_passes_operator_options_in_order(
    tmp_path: Path,
    options: tuple[tuple[str, str], ...],
) -> None:
    runner = FakeRunner()

    proposal = generate(evidence(tmp_path, request(NEW, *options)), runner)

    arguments = expected_arguments(
        NEW, *(part for option in options for part in option)
    )
    assert runner.calls[0]["arguments"] == arguments
    assert patch_changes(proposal.patch or "") == [
        (path, "added") for path in sorted(overlay(arguments))
    ]


def test_generator_patch_applies_and_yields_a_complete_definition(
    tmp_path: Path,
) -> None:
    root = fleet(tmp_path)
    options = (("--htpasswd-provider-name", "Acme Corp"),)
    proposal = generate(
        evidence(tmp_path, request(NEW, *options, tier="ai"), root=root),
        FakeRunner(),
    )

    apply(root, proposal)

    snapshot = GitInventoryCollector(root).collect(command_id=UUID(int=2))
    [definition] = [
        item for item in snapshot.items if item.data.get("cluster_name") == NEW
    ]
    assert definition.kind == "cluster_definition"
    assert definition.data["path"] == f"provision/{NEW}"
    assert definition.data["status"] == "active"
    assert definition.data["cloud_provider"] == "aws"
    assert definition.data["region"] == REGION
    assert definition.data["tier"] == "ai"
    assert yaml.safe_load(
        (root / "provision" / NEW / "metadata.yaml").read_text()
    ) == {
        "platform": "aws",
        "aws-credentials-mode": "scoped",
        "htpasswd-provider-name": "Acme Corp",
    }


def test_generator_states_the_provision_chain_and_commit(
    tmp_path: Path,
) -> None:
    proposal = generate(evidence(tmp_path), FakeRunner())

    assert proposal.trigger_effect == [
        TriggerEffect.PRE_PROVISION,
        TriggerEffect.PROVISION,
        TriggerEffect.POST_PROVISION,
    ]
    assert proposal.reversibility is Reversibility.REVERSIBLE_BEFORE_MERGE
    assert "deprovision" in proposal.rollback
    assert NEW in (proposal.commit_message or "")
    assert SKIP_PROVISION not in (proposal.commit_message or "")
    assert proposal.commit_message.rstrip().endswith(
        f"Aura-Proposal: {proposal.proposal_id}"
    )
    assert any("pre-provision" in risk for risk in proposal.risks)
    assert proposal.scaffold_commit in (proposal.commit_message or "")
    assert " ".join(expected_arguments()) in (proposal.commit_message or "")


def test_generator_refuses_what_the_definition_check_reports(
    tmp_path: Path,
) -> None:
    root = fleet(tmp_path)
    check = PassingCheck("applying the patch yields nothing")
    runner = FakeRunner()

    with pytest.raises(ProposalRefused) as refusal:
        generate(evidence(tmp_path, root=root), runner, check)

    assert refusal.value.reasons == ["applying the patch yields nothing"]
    [call] = check.calls
    assert call["base_commit"] == head(root)
    assert call["cluster_name"] == NEW
    assert patch_changes(call["patch"]) == [
        (path, "added")
        for path in sorted(overlay(runner.calls[0]["arguments"]))
    ]


def test_generator_passes_the_real_definition_check(tmp_path: Path) -> None:
    root = fleet(tmp_path)

    proposal = generate(
        evidence(tmp_path, root=root),
        FakeRunner(),
        GitDefinitionCheck(root),
    )

    assert proposal.cluster_name == NEW


def test_generator_is_deterministic(tmp_path: Path) -> None:
    snapshot = evidence(tmp_path)

    assert generate(snapshot, FakeRunner()) == generate(snapshot, FakeRunner())


def test_scaffold_proposal_goes_stale_when_its_paths_change(
    tmp_path: Path,
) -> None:
    root = fleet(tmp_path)
    proposal = generate(evidence(tmp_path, root=root), FakeRunner())
    staleness = GitProposalStaleness(root)

    assert staleness.check(proposal).stale is False

    write_cluster(root, cluster_name=NEW)
    commit_repository(root)

    assert staleness.check(proposal).stale is True


def test_generator_redacts_secret_lines_in_scaffold_streams(
    tmp_path: Path,
) -> None:
    runner = FakeRunner(
        stdout="aws_secret_access_key: abc123\nwrote overlay\n",
        stderr="token=xyz789\nCluster scaffolded successfully\n",
    )

    run = generate(evidence(tmp_path), runner).scaffold_run

    assert run is not None
    assert "abc123" not in run.stdout and "wrote overlay" in run.stdout
    assert "xyz789" not in run.stderr
    assert "Cluster scaffolded successfully" in run.stderr


@pytest.mark.parametrize(
    "line",
    [
        "ERROR: token=abc123",
        "X-Api-Key: abc123",
        "export AWS_SECRET_ACCESS_KEY=abc123",
        "pull secret: abc123",
        "aws_secret_access_key\tabc123",
        "Authorization: Bearer abc123",
        '{"auths": {"quay.io": {"auth": "abc123"}}}',
    ],
)
def test_generator_redacts_each_secret_line_shape(
    tmp_path: Path,
    line: str,
) -> None:
    runner = FakeRunner(stderr=f"{line}\nCluster scaffolded successfully\n")

    run = generate(evidence(tmp_path), runner).scaffold_run

    assert run is not None
    assert "abc123" not in run.stderr
    assert "Cluster scaffolded successfully" in run.stderr


def test_generator_redacts_private_key_blocks(tmp_path: Path) -> None:
    runner = FakeRunner(
        stdout=(
            "before\n-----BEGIN RSA PRIVATE KEY-----\nMIIEabc123\n"
            "-----END RSA PRIVATE KEY-----\nafter\n"
        )
    )

    run = generate(evidence(tmp_path), runner).scaffold_run

    assert run is not None
    assert "MIIEabc123" not in run.stdout
    assert run.stdout.startswith("before\n")
    assert run.stdout.endswith("after\n")


def test_generator_keeps_carriage_returns_in_generated_files(
    tmp_path: Path,
) -> None:
    root = fleet(tmp_path)
    path = f"provision/{NEW}/hive/patches/notes.yaml"

    def crlf(files: dict[str, tuple[str, str]]) -> None:
        files[path] = (REGULAR, "a: b\r\nc: d\r\n")

    apply(root, generate(evidence(tmp_path, root=root), FakeRunner(crlf)))

    assert (root / path).read_bytes() == b"a: b\r\nc: d\r\n"


def test_collector_records_the_checkout_without_its_local_path(
    tmp_path: Path,
) -> None:
    snapshot = evidence(tmp_path)

    [checkout] = [i for i in snapshot.items if i.kind == "scaffold_checkout"]
    assert checkout.data == {
        "commit": head(tmp_path / "fleet-cluster-scaffold"),
        "clean": True,
    }


def test_collector_rejects_a_directory_inside_a_checkout(
    tmp_path: Path,
) -> None:
    root = scaffold_checkout(tmp_path / "outer")

    with pytest.raises(ScaffoldCheckoutError, match="not the top"):
        evidence(tmp_path, checkout=root / "src")


def test_generator_lets_runner_failures_fail_the_command(
    tmp_path: Path,
) -> None:
    runner = FakeRunner(error=ScaffoldRunError("scaffold timed out"))

    with pytest.raises(ScaffoldRunError, match="scaffold timed out"):
        generate(evidence(tmp_path), runner)


# Refusals before the scaffold runs


PREFIX_21 = "abcdefghijklmnopqrstu"


@pytest.mark.parametrize(
    ("build", "reason"),
    [
        (
            lambda tmp: evidence(tmp, request(cloud="azure")),
            "scaffold supports only cloud aws, not azure",
        ),
        (
            lambda tmp: evidence(tmp, request("Bad_Name")),
            "cluster name Bad_Name is not a DNS-1123 label",
        ),
        (
            lambda tmp: evidence(tmp, request("a" * 50)),
            f"cluster name {'a' * 50} is 50 characters; scoped AWS names "
            "allow at most 49",
        ),
        (
            lambda tmp: evidence(tmp, request(tier="gold")),
            "tier gold is not one of base, virt, ai",
        ),
        (
            lambda tmp: evidence(tmp, request(NEW, ("--topology", "bogus"))),
            "topology bogus is not one of standard, compact, sno",
        ),
        (
            lambda tmp: evidence(tmp, root=fleet(tmp, names=(NEW,))),
            f"cluster {NEW} already has a definition at provision/{NEW}",
        ),
        (
            lambda tmp: evidence(tmp, root=fleet(tmp, archived=(NEW,))),
            f"cluster {NEW} already has a definition at archive/{NEW}",
        ),
        (
            lambda tmp: evidence(tmp, root=fleet(tmp, sentinels=(NEW,))),
            f"deprovision sentinel already exists: deprovision/{NEW}",
        ),
        (
            lambda tmp: evidence(
                tmp, root=fleet(tmp, sentinels=(f"{NEW}-archive",))
            ),
            f"deprovision sentinel already exists: deprovision/{NEW}-archive",
        ),
        (
            lambda tmp: evidence(tmp, openshift=(NEW,)),
            f"OpenShift resources already exist for cluster {NEW}",
        ),
        (
            lambda tmp: evidence(tmp, openshift=(), managed=(NEW,)),
            f"OpenShift resources already exist for cluster {NEW}",
        ),
        (
            lambda tmp: evidence(tmp, root=fleet(tmp, names=(f"{NEW}-archive",))),
            f"cluster {NEW} already has a definition at provision/{NEW}-archive",
        ),
        (
            lambda tmp: evidence(
                tmp, root=fleet(tmp, archived=(f"{NEW}-archive",))
            ),
            f"cluster {NEW} already has a definition at archive/{NEW}-archive",
        ),
        (
            lambda tmp: evidence(
                tmp,
                request(f"{PREFIX_21}-two"),
                openshift=(),
                managed=(f"{PREFIX_21}-x",),
            ),
            f"infra prefix {PREFIX_21} is also used by OpenShift cluster "
            f"{PREFIX_21}-x",
        ),
        (
            lambda tmp: evidence(
                tmp, request(NEW, ("--environment", "--force"))
            ),
            "scaffold option --environment value --force starts with -",
        ),
        (
            lambda tmp: evidence(tmp, request(NEW, ("--environment", ""))),
            "scaffold option --environment has no value",
        ),
        *(
            (
                lambda tmp, region=region: evidence(
                    tmp, request(region=region)
                ),
                f"region {region} is not an AWS region name",
            )
            for region in ("--zones", "us east 1", "")
        ),
        (
            lambda tmp: evidence(
                tmp,
                request(f"{PREFIX_21}-two"),
                root=fleet(tmp, names=(f"{PREFIX_21}-one",)),
            ),
            f"infra prefix {PREFIX_21} is also used by "
            f"provision/{PREFIX_21}-one",
        ),
        (
            lambda tmp: evidence(
                tmp,
                request(f"{PREFIX_21}-two"),
                root=fleet(tmp, archived=(f"{PREFIX_21}-one",)),
            ),
            f"infra prefix {PREFIX_21} is also used by "
            f"archive/{PREFIX_21}-one",
        ),
        (
            lambda tmp: evidence(
                tmp,
                request(f"{PREFIX_21}-two"),
                root=fleet(tmp, sentinels=(f"{PREFIX_21}-one-archive",)),
            ),
            f"infra prefix {PREFIX_21} is also used by "
            f"deprovision/{PREFIX_21}-one-archive",
        ),
        (
            lambda tmp: evidence(
                tmp, request(f"{PREFIX_21}-two"), openshift=(f"{PREFIX_21}-x",)
            ),
            f"infra prefix {PREFIX_21} is also used by OpenShift cluster "
            f"{PREFIX_21}-x",
        ),
        (
            lambda tmp: evidence(
                tmp,
                request("abcdefghijklmnopqrst"),
                root=fleet(tmp, names=("abcdefghijklmnopqrst-x",)),
            ),
            "infra prefix abcdefghijklmnopqrst is also used by "
            "provision/abcdefghijklmnopqrst-x",
        ),
        (
            lambda tmp: evidence(tmp, request(NEW, ("--force", "yes"))),
            "scaffold option --force is not allowed",
        ),
        (
            lambda tmp: evidence(
                tmp, request(NEW, ("--zones", "a"), ("--zones", "b"))
            ),
            "scaffold option --zones is repeated",
        ),
        (
            lambda tmp: evidence(tmp, kustomize=None),
            "kustomize is not on PATH",
        ),
        (
            lambda tmp: evidence(tmp, dirty=True),
            "scaffold checkout has uncommitted changes to tracked files",
        ),
        (
            lambda tmp: evidence(tmp, git=FakeGitCollector()),
            "no pinned Git base commit in evidence",
        ),
    ],
)
def test_generator_refuses_before_running_the_scaffold(
    tmp_path: Path,
    build: Callable[[Path], EvidenceSnapshot],
    reason: str,
) -> None:
    runner = FakeRunner()

    with pytest.raises(ProposalRefused) as refusal:
        generate(build(tmp_path), runner)

    assert reason in refusal.value.reasons
    assert runner.calls == []


def test_generator_allows_a_prefix_that_differs_within_21_characters(
    tmp_path: Path,
) -> None:
    root = fleet(tmp_path, names=("abcdefghijklmnopqrsx-one",))

    proposal = generate(
        evidence(tmp_path, request("abcdefghijklmnopqrsy-one"), root=root),
        FakeRunner(),
    )

    assert proposal.cluster_name == "abcdefghijklmnopqrsy-one"


# Refusals after the scaffold runs


def _outside(files: dict[str, tuple[str, str]]) -> None:
    files["cluster-templates/aws/standard/extra.yaml"] = (REGULAR, "a: b\n")


def _symlink(files: dict[str, tuple[str, str]]) -> None:
    files[f"provision/{NEW}/link.yaml"] = ("120000", "metadata.yaml")


def _secret(files: dict[str, tuple[str, str]]) -> None:
    files[f"provision/{NEW}/hive/patches/secret.yaml"] = (
        REGULAR,
        "stringData:\n  password: hunter2\n",
    )


def _legacy(files: dict[str, tuple[str, str]]) -> None:
    files[f"provision/{NEW}/metadata.yaml"] = (
        REGULAR,
        "platform: aws\naws-credentials-mode: legacy\n",
    )


def _no_current_pattern(files: dict[str, tuple[str, str]]) -> None:
    del files[f"provision/{NEW}/crossplane/patches/machine-api/policy.yaml"]


def _stale_pattern(files: dict[str, tuple[str, str]]) -> None:
    files[f"provision/{NEW}/crossplane/patches/machine-api/policy.yaml"] = (
        REGULAR,
        "value: deny-all\n",
    )


def _old_render(files: dict[str, tuple[str, str]]) -> None:
    files[f"provision/{NEW}/crossplane/patches/installer/policy.yaml"] = (
        REGULAR,
        f"value: 'arn:aws:iam::*:instance-profile/{NEW}-*'\n",
    )


def _unparsable(files: dict[str, tuple[str, str]]) -> None:
    files[f"provision/{NEW}/hive/patches/broken.yaml"] = (
        REGULAR,
        "password: hunter2\nbad: [\n",
    )


def _empty(files: dict[str, tuple[str, str]]) -> None:
    files.clear()


@pytest.mark.parametrize(
    ("runner", "reason"),
    [
        (
            FakeRunner(exit_code=1, stderr="line one\nbad --zones value\n"),
            "scaffold exited 1: bad --zones value",
        ),
        (
            FakeRunner(exit_code=1, stderr="password: hunter2\n"),
            "scaffold exited 1: [REDACTED]",
        ),
        (
            FakeRunner(_unparsable),
            f"scaffold output provision/{NEW}/hive/patches/broken.yaml is not "
            "valid YAML, so it cannot be checked for secrets",
        ),
        *(
            (
                FakeRunner(
                    lambda files, marker=marker: files.__setitem__(
                        f"provision/{NEW}/crossplane/patches/x/policy.yaml",
                        (REGULAR, f"value: '{marker}'\n"),
                    )
                ),
                f"provision/{NEW}/crossplane/patches/x/policy.yaml contains "
                f"the old render pattern {marker}",
            )
            for marker in (
                f"s3:::{NEW}-*",
                f"user/{NEW}-*",
                f"role/{NEW}-*",
            )
        ),
        (
            FakeRunner(_outside),
            f"scaffold wrote outside provision/{NEW}/: "
            "['cluster-templates/aws/standard/extra.yaml']",
        ),
        (
            FakeRunner(_symlink),
            f"scaffold wrote a non-regular file provision/{NEW}/link.yaml "
            "with mode 120000",
        ),
        (
            FakeRunner(_secret),
            f"scaffold output provision/{NEW}/hive/patches/secret.yaml "
            "contains secret-named fields: ['stringData.password']",
        ),
        (
            FakeRunner(_legacy),
            "scaffold metadata.yaml must declare platform aws and "
            "aws-credentials-mode scoped",
        ),
        (
            FakeRunner(_no_current_pattern),
            f"provision/{NEW}/crossplane/patches/machine-api/policy.yaml "
            "is missing",
        ),
        (
            FakeRunner(_stale_pattern),
            f"provision/{NEW}/crossplane/patches/machine-api/policy.yaml "
            f"lacks instance-profile/{NEW}-?????-worker-profile",
        ),
        (
            FakeRunner(_old_render),
            f"provision/{NEW}/crossplane/patches/installer/policy.yaml "
            f"contains the old render pattern instance-profile/{NEW}-*",
        ),
        (FakeRunner(_empty), "scaffold wrote no files"),
        (
            FakeRunner(
                changed=(
                    "modified cluster-templates/marker.txt",
                    "special provision/new-cluster/pipe",
                )
            ),
            "scaffold changed files other than adding them: "
            "['modified cluster-templates/marker.txt', "
            "'special provision/new-cluster/pipe']",
        ),
    ],
)
def test_generator_refuses_unusable_scaffold_output(
    tmp_path: Path,
    runner: FakeRunner,
    reason: str,
) -> None:
    with pytest.raises(ProposalRefused) as refusal:
        generate(evidence(tmp_path), runner)

    assert reason in refusal.value.reasons
    assert len(runner.calls) == 1


# Infra prefix parity with fleet-clusters scripts/check_new_overlays.py


@pytest.mark.parametrize(
    ("name", "prefix"),
    [
        ("acme", "acme"),
        ("abcdefghijklmnopqrstuvwxyz", "abcdefghijklmnopqrstu"),
        ("abcdefghijklmnopqrst-x", "abcdefghijklmnopqrst"),
        ("a--b", "a-b"),
        ("a_b.c", "a-b-c"),
        ("0171d46a-scopedaws", "0171d46a-scopedaws"),
    ],
)
def test_infra_prefix_matches_the_installer_rule(name: str, prefix: str) -> None:
    assert infra_prefix(name) == prefix
