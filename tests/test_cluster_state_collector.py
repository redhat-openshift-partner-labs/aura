from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import final
from uuid import UUID

import pytest

from aura.collectors.cluster_state import ClusterStateCollector
from aura.collectors.git_inventory import GitInventoryCollector
from aura.contracts import EvidenceItem, EvidenceSnapshot
from test_git_inventory_collector import commit_repository, write_cluster


@final
class FakeGitCollector:
    def collect(
        self,
        *,
        command_id: UUID,
        snapshot_id: UUID | None = None,
        collected_at: datetime | None = None,
        source_version: str | None = None,
    ) -> EvidenceSnapshot:
        return EvidenceSnapshot(
            snapshot_id=snapshot_id or UUID(int=10),
            command_id=command_id,
            collector="git_inventory",
            collector_version="test",
            collected_at=collected_at or datetime(2026, 1, 1, tzinfo=UTC),
            source="fleet-clusters",
            source_version=source_version or "git-version",
            items=[
                EvidenceItem(
                    item_id=UUID(int=11),
                    kind="cluster_definition",
                    data={
                        "cluster_name": "example",
                        "path": "provision/example",
                    },
                )
            ],
            redactions=[],
        )


@final
class ConflictingGitCollector:
    def collect(
        self,
        *,
        command_id: UUID,
        snapshot_id: UUID | None = None,
        collected_at: datetime | None = None,
        source_version: str | None = None,
    ) -> EvidenceSnapshot:
        return EvidenceSnapshot(
            snapshot_id=snapshot_id or UUID(int=10),
            command_id=command_id,
            collector="git_inventory",
            collector_version="test",
            collected_at=collected_at or datetime(2026, 1, 1, tzinfo=UTC),
            source="fleet-clusters",
            source_version=source_version or "git-version",
            items=[
                EvidenceItem(
                    item_id=UUID(int=11),
                    kind="cluster_definition",
                    data={
                        "cluster_name": "example",
                        "path": "provision/example",
                        "status": "active",
                    },
                ),
                EvidenceItem(
                    item_id=UUID(int=12),
                    kind="cluster_definition",
                    data={
                        "cluster_name": "example",
                        "path": "archive/example",
                        "status": "archived",
                    },
                ),
            ],
            redactions=[],
        )


@final
class IncompleteGitCollector:
    def collect(
        self,
        *,
        command_id: UUID,
        snapshot_id: UUID | None = None,
        collected_at: datetime | None = None,
        source_version: str | None = None,
    ) -> EvidenceSnapshot:
        return EvidenceSnapshot(
            snapshot_id=snapshot_id or UUID(int=10),
            command_id=command_id,
            collector="git_inventory",
            collector_version="test",
            collected_at=collected_at or datetime(2026, 1, 1, tzinfo=UTC),
            source="fleet-clusters",
            source_version=source_version or "git-version",
            items=[
                EvidenceItem(
                    item_id=UUID(int=11),
                    kind="cluster_definition_incomplete",
                    data={
                        "cluster_name": "example",
                        "path": "provision/example",
                        "missing_files": ["clusters/example.yaml"],
                    },
                )
            ],
            redactions=[],
        )


@final
class FakeOpenShiftCollector:
    def collect(
        self,
        *,
        command_id: UUID,
        snapshot_id: UUID | None = None,
        collected_at: datetime | None = None,
        source_version: str | None = None,
    ) -> EvidenceSnapshot:
        return EvidenceSnapshot(
            snapshot_id=snapshot_id or UUID(int=20),
            command_id=command_id,
            collector="openshift_inventory",
            collector_version="test",
            collected_at=collected_at or datetime(2026, 1, 1, tzinfo=UTC),
            source="openshift",
            source_version=source_version or "ocp-version",
            items=[
                EvidenceItem(
                    item_id=UUID(int=21),
                    kind="cluster_actual_state",
                    data={
                        "cluster_name": "example",
                        "namespace": "example-ns",
                        "cluster_deployment_conditions": [
                            {
                                "type": "Provisioned",
                                "status": "True",
                                "reason": "Provisioned",
                            }
                        ],
                    },
                )
            ],
            redactions=[],
        )


@final
class ConditionOpenShiftCollector:
    def __init__(self, condition: dict[str, str] | None) -> None:
        self._condition = condition

    def collect(
        self,
        *,
        command_id: UUID,
        snapshot_id: UUID | None = None,
        collected_at: datetime | None = None,
        source_version: str | None = None,
    ) -> EvidenceSnapshot:
        conditions = [] if self._condition is None else [self._condition]
        return EvidenceSnapshot(
            snapshot_id=snapshot_id or UUID(int=20),
            command_id=command_id,
            collector="openshift_inventory",
            collector_version="test",
            collected_at=collected_at or datetime(2026, 1, 1, tzinfo=UTC),
            source="openshift",
            source_version=source_version or "ocp-version",
            items=[
                EvidenceItem(
                    item_id=UUID(int=21),
                    kind="cluster_actual_state",
                    data={
                        "cluster_name": "example",
                        "namespace": "example-ns",
                        "cluster_deployment_conditions": conditions,
                    },
                )
            ],
            redactions=[],
        )


@final
class FakeTektonCollector:
    def __init__(self, status: str = "Failed") -> None:
        self._status = status

    def collect(
        self,
        *,
        command_id: UUID,
        snapshot_id: UUID | None = None,
        collected_at: datetime | None = None,
        source_version: str | None = None,
    ) -> EvidenceSnapshot:
        return EvidenceSnapshot(
            snapshot_id=snapshot_id or UUID(int=30),
            command_id=command_id,
            collector="tekton_inventory",
            collector_version="test",
            collected_at=collected_at or datetime(2026, 1, 1, tzinfo=UTC),
            source="tekton",
            source_version=source_version or "tekton-version",
            items=[
                EvidenceItem(
                    item_id=UUID(int=31),
                    kind="pipeline_run",
                    data={
                        "namespace": "example-ns",
                        "pipeline_run_name": "deploy-cluster-abc",
                        "status": self._status,
                        "failed_task": (
                            {"name": "provision-task-abc"}
                            if self._status == "Failed"
                            else None
                        ),
                    },
                )
            ],
            redactions=[],
        )


@final
class MultipleTektonCollector:
    def collect(
        self,
        *,
        command_id: UUID,
        snapshot_id: UUID | None = None,
        collected_at: datetime | None = None,
        source_version: str | None = None,
    ) -> EvidenceSnapshot:
        return EvidenceSnapshot(
            snapshot_id=snapshot_id or UUID(int=30),
            command_id=command_id,
            collector="tekton_inventory",
            collector_version="test",
            collected_at=collected_at or datetime(2026, 1, 1, tzinfo=UTC),
            source="tekton",
            source_version=source_version or "tekton-version",
            items=[
                EvidenceItem(
                    item_id=UUID(int=31),
                    kind="pipeline_run",
                    data={
                        "pipeline_run_id": (
                            "0171d46a-0000-0000-0000-000000000031"
                        ),
                        "pipeline_run_name": "deploy-cluster-older",
                        "namespace": "example-ns",
                        "cluster_name": "example",
                        "status": "Failed",
                        "start_time": "2026-01-01T00:00:00Z",
                    },
                ),
                EvidenceItem(
                    item_id=UUID(int=32),
                    kind="pipeline_run",
                    data={
                        "pipeline_run_id": (
                            "0171d46a-0000-0000-0000-000000000032"
                        ),
                        "pipeline_run_name": "deploy-cluster-newer",
                        "namespace": "example-ns",
                        "cluster_name": "example",
                        "status": "Succeeded",
                        "start_time": "2026-01-02T00:00:00Z",
                    },
                ),
            ],
            redactions=[],
        )


@final
class EmptyCollector:
    def collect(
        self,
        *,
        command_id: UUID,
        snapshot_id: UUID | None = None,
        collected_at: datetime | None = None,
        source_version: str | None = None,
    ) -> EvidenceSnapshot:
        return EvidenceSnapshot(
            snapshot_id=snapshot_id or UUID(int=60),
            command_id=command_id,
            collector="empty",
            collector_version="test",
            collected_at=collected_at or datetime(2026, 1, 1, tzinfo=UTC),
            source="empty",
            source_version=source_version or "empty",
            items=[],
            redactions=[],
        )


def collect_cluster_state(
    *,
    git_collector: object,
    openshift_collector: object,
    tekton_collector: object,
    pipeline_run: str | None = None,
) -> EvidenceSnapshot:
    return ClusterStateCollector(
        git_collector=git_collector,
        openshift_collector=openshift_collector,
        tekton_collector=tekton_collector,
        cluster_name="example",
        pipeline_run=pipeline_run,
    ).collect(
        command_id=UUID(int=1),
        snapshot_id=UUID(int=40),
        collected_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def test_cluster_state_collector_correlates_evidence() -> None:
    snapshot = collect_cluster_state(
        git_collector=FakeGitCollector(),
        openshift_collector=FakeOpenShiftCollector(),
        tekton_collector=FakeTektonCollector(),
    )

    assert snapshot.collector == "cluster_state"
    assert snapshot.source == "git+openshift+tekton"
    assert snapshot.source_version == (
        "git=git-version;openshift=ocp-version;tekton=tekton-version"
    )
    assert len(snapshot.items) == 4

    correlation = snapshot.items[-1]
    assert correlation.kind == "cluster_state_correlation"
    assert correlation.data == {
        "cluster_name": "example",
        "namespace": "example-ns",
        "git_evidence_id": str(UUID(int=11)),
        "git_evidence_ids": [str(UUID(int=11))],
        "definition_conflict": False,
        "openshift_evidence_id": str(UUID(int=21)),
        "pipeline_run_evidence_ids": [str(UUID(int=31))],
        "deprovision_status": None,
        "deprovision_sentinel_evidence_ids": [],
        "missing_evidence": [],
    }


def test_cluster_state_collector_reports_missing_sources() -> None:
    snapshot = collect_cluster_state(
        git_collector=EmptyCollector(),
        openshift_collector=EmptyCollector(),
        tekton_collector=EmptyCollector(),
    )

    correlation = snapshot.items[-1]
    assert correlation.data["missing_evidence"] == [
        "Git cluster definition",
        "OpenShift actual state",
        "Tekton PipelineRun status",
    ]


def test_cluster_state_collector_reports_definition_conflict() -> None:
    snapshot = collect_cluster_state(
        git_collector=ConflictingGitCollector(),
        openshift_collector=FakeOpenShiftCollector(),
        tekton_collector=FakeTektonCollector(),
    )

    correlation = snapshot.items[-1]
    assert len(snapshot.items) == 5
    assert correlation.data["git_evidence_id"] is None
    assert correlation.data["git_evidence_ids"] == [
        str(UUID(int=11)),
        str(UUID(int=12)),
    ]
    assert correlation.data["definition_conflict"] is True
    assert correlation.data["missing_evidence"] == [
        "Conflicting Git cluster definitions"
    ]


def test_cluster_state_collector_reports_incomplete_definition() -> None:
    snapshot = collect_cluster_state(
        git_collector=IncompleteGitCollector(),
        openshift_collector=FakeOpenShiftCollector(),
        tekton_collector=FakeTektonCollector(),
    )

    correlation = snapshot.items[-1]
    assert len(snapshot.items) == 4
    assert snapshot.items[0].kind == "cluster_definition_incomplete"
    assert correlation.data["git_evidence_id"] == str(UUID(int=11))
    assert correlation.data["git_evidence_ids"] == [str(UUID(int=11))]
    assert correlation.data["definition_conflict"] is False
    assert correlation.data["missing_evidence"] == [
        "Git cluster definition files"
    ]


def test_cluster_state_collector_matches_git_definition_without_metadata(
    tmp_path: Path,
) -> None:
    write_cluster(tmp_path)
    (tmp_path / "provision" / "example" / "metadata.yaml").unlink()
    commit_repository(tmp_path)
    snapshot = collect_cluster_state(
        git_collector=GitInventoryCollector(tmp_path),
        openshift_collector=FakeOpenShiftCollector(),
        tekton_collector=FakeTektonCollector(),
    )

    correlation = snapshot.items[-1]
    assert snapshot.items[0].kind == "cluster_definition_incomplete"
    assert correlation.data["git_evidence_id"] == str(
        snapshot.items[0].item_id
    )
    assert correlation.data["missing_evidence"] == [
        "Git cluster definition files"
    ]


def test_cluster_state_collector_refuses_invalid_git_definition(
    tmp_path: Path,
) -> None:
    write_cluster(tmp_path)
    (
        tmp_path / "provision" / "example" / "metadata.yaml"
    ).write_text("platform: [aws\n", encoding="utf-8")
    commit_repository(tmp_path)
    snapshot = collect_cluster_state(
        git_collector=GitInventoryCollector(tmp_path),
        openshift_collector=FakeOpenShiftCollector(),
        tekton_collector=FakeTektonCollector(),
    )

    assert snapshot.items[0].kind == "cluster_definition_invalid"
    assert snapshot.items[-1].data["missing_evidence"] == [
        "Valid Git cluster definition"
    ]


def write_deprovisioned_cluster(
    root: Path,
    sentinel: str,
    *,
    directory: str = "provision",
) -> None:
    write_cluster(root, directory=directory)
    (root / "deprovision").mkdir()
    (root / "deprovision" / sentinel).write_text("", encoding="utf-8")
    commit_repository(root)


@pytest.mark.parametrize(
    ("sentinel", "status"),
    [("example", "stranded"), ("example-archive", "archive-pending")],
)
def test_cluster_state_collector_expects_no_openshift_after_deprovision(
    tmp_path: Path,
    sentinel: str,
    status: str,
) -> None:
    write_deprovisioned_cluster(tmp_path, sentinel)

    snapshot = collect_cluster_state(
        git_collector=GitInventoryCollector(tmp_path),
        openshift_collector=EmptyCollector(),
        tekton_collector=MultipleTektonCollector(),
    )

    correlation = snapshot.items[-1]
    sentinel_item = next(
        item for item in snapshot.items if item.kind == "deprovision_sentinel"
    )
    assert correlation.data["deprovision_status"] == status
    assert correlation.data["deprovision_sentinel_evidence_ids"] == [
        str(sentinel_item.item_id)
    ]
    assert correlation.data["missing_evidence"] == []


@pytest.mark.parametrize(
    ("metadata", "kind"),
    [
        (None, "cluster_definition_incomplete"),
        ("platform: [aws\n", "cluster_definition_invalid"),
    ],
)
def test_cluster_state_collector_explains_deprovision_of_legacy_definition(
    tmp_path: Path,
    metadata: str | None,
    kind: str,
) -> None:
    write_cluster(tmp_path)
    metadata_path = tmp_path / "provision" / "example" / "metadata.yaml"
    if metadata is None:
        metadata_path.unlink()
    else:
        metadata_path.write_text(metadata, encoding="utf-8")
    (tmp_path / "deprovision").mkdir()
    (tmp_path / "deprovision" / "example").write_text("", encoding="utf-8")
    commit_repository(tmp_path)

    snapshot = collect_cluster_state(
        git_collector=GitInventoryCollector(tmp_path),
        openshift_collector=EmptyCollector(),
        tekton_collector=MultipleTektonCollector(),
    )

    assert snapshot.items[0].kind == kind
    assert snapshot.items[-1].data["deprovision_status"] == "stranded"
    assert snapshot.items[-1].data["missing_evidence"] == []


def test_cluster_state_collector_requires_openshift_for_leftover_sentinel(
    tmp_path: Path,
) -> None:
    write_deprovisioned_cluster(tmp_path, "example", directory="archive")

    snapshot = collect_cluster_state(
        git_collector=GitInventoryCollector(tmp_path),
        openshift_collector=EmptyCollector(),
        tekton_collector=MultipleTektonCollector(),
    )

    correlation = snapshot.items[-1]
    assert correlation.data["deprovision_status"] == "leftover"
    assert correlation.data["missing_evidence"] == ["OpenShift actual state"]


def test_cluster_state_collector_selects_latest_pipeline_run() -> None:
    snapshot = collect_cluster_state(
        git_collector=FakeGitCollector(),
        openshift_collector=FakeOpenShiftCollector(),
        tekton_collector=MultipleTektonCollector(),
    )

    correlation = snapshot.items[-1]
    assert snapshot.items[-2].data["pipeline_run_name"] == (
        "deploy-cluster-newer"
    )
    assert correlation.data["pipeline_run_evidence_ids"] == [
        str(UUID(int=32))
    ]
    assert correlation.data["missing_evidence"] == []


def test_cluster_state_collector_selects_pipeline_run_by_id() -> None:
    snapshot = collect_cluster_state(
        git_collector=FakeGitCollector(),
        openshift_collector=FakeOpenShiftCollector(),
        tekton_collector=MultipleTektonCollector(),
        pipeline_run="0171d46a-0000-0000-0000-000000000031",
    )

    correlation = snapshot.items[-1]
    assert snapshot.items[-2].data["pipeline_run_name"] == (
        "deploy-cluster-older"
    )
    assert correlation.data["pipeline_run_evidence_ids"] == [
        str(UUID(int=31))
    ]
    assert correlation.data["missing_evidence"] == []


def test_cluster_state_collector_selects_pipeline_run_by_name() -> None:
    snapshot = collect_cluster_state(
        git_collector=FakeGitCollector(),
        openshift_collector=FakeOpenShiftCollector(),
        tekton_collector=MultipleTektonCollector(),
        pipeline_run="deploy-cluster-older",
    )

    correlation = snapshot.items[-1]
    assert correlation.data["pipeline_run_evidence_ids"] == [
        str(UUID(int=31))
    ]


def test_cluster_state_collector_reports_missing_selected_run() -> None:
    snapshot = collect_cluster_state(
        git_collector=FakeGitCollector(),
        openshift_collector=FakeOpenShiftCollector(),
        tekton_collector=MultipleTektonCollector(),
        pipeline_run="missing-run",
    )

    correlation = snapshot.items[-1]
    assert len(snapshot.items) == 3
    assert correlation.data["pipeline_run_evidence_ids"] == []
    assert correlation.data["missing_evidence"] == [
        "Selected Tekton PipelineRun missing-run"
    ]
