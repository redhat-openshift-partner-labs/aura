from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import final
from uuid import UUID

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
                        "status": "Failed",
                        "failed_task": {
                            "name": "provision-task-abc",
                        },
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


def test_cluster_state_collector_correlates_evidence() -> None:
    collector = ClusterStateCollector(
        git_collector=FakeGitCollector(),
        openshift_collector=FakeOpenShiftCollector(),
        tekton_collector=FakeTektonCollector(),
        cluster_name="example",
    )

    snapshot = collector.collect(
        command_id=UUID(int=1),
        snapshot_id=UUID(int=40),
        collected_at=datetime(2026, 1, 1, tzinfo=UTC),
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
        "missing_evidence": [],
    }


def test_cluster_state_collector_reports_missing_sources() -> None:
    collector = ClusterStateCollector(
        git_collector=EmptyCollector(),
        openshift_collector=EmptyCollector(),
        tekton_collector=EmptyCollector(),
        cluster_name="example",
    )

    snapshot = collector.collect(
        command_id=UUID(int=1),
        snapshot_id=UUID(int=40),
        collected_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    correlation = snapshot.items[-1]
    assert correlation.data["missing_evidence"] == [
        "Git cluster definition",
        "OpenShift actual state",
        "Tekton PipelineRun status",
    ]


def test_cluster_state_collector_reports_definition_conflict() -> None:
    collector = ClusterStateCollector(
        git_collector=ConflictingGitCollector(),
        openshift_collector=FakeOpenShiftCollector(),
        tekton_collector=FakeTektonCollector(),
        cluster_name="example",
    )

    snapshot = collector.collect(
        command_id=UUID(int=1),
        snapshot_id=UUID(int=40),
        collected_at=datetime(2026, 1, 1, tzinfo=UTC),
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
    collector = ClusterStateCollector(
        git_collector=IncompleteGitCollector(),
        openshift_collector=FakeOpenShiftCollector(),
        tekton_collector=FakeTektonCollector(),
        cluster_name="example",
    )

    snapshot = collector.collect(
        command_id=UUID(int=1),
        snapshot_id=UUID(int=40),
        collected_at=datetime(2026, 1, 1, tzinfo=UTC),
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
    collector = ClusterStateCollector(
        git_collector=GitInventoryCollector(tmp_path),
        openshift_collector=FakeOpenShiftCollector(),
        tekton_collector=FakeTektonCollector(),
        cluster_name="example",
    )

    snapshot = collector.collect(
        command_id=UUID(int=1),
        snapshot_id=UUID(int=40),
        collected_at=datetime(2026, 1, 1, tzinfo=UTC),
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
    collector = ClusterStateCollector(
        git_collector=GitInventoryCollector(tmp_path),
        openshift_collector=FakeOpenShiftCollector(),
        tekton_collector=FakeTektonCollector(),
        cluster_name="example",
    )

    snapshot = collector.collect(
        command_id=UUID(int=1),
        snapshot_id=UUID(int=40),
        collected_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    assert snapshot.items[0].kind == "cluster_definition_invalid"
    assert snapshot.items[-1].data["missing_evidence"] == [
        "Valid Git cluster definition"
    ]


def test_cluster_state_collector_selects_latest_pipeline_run() -> None:
    collector = ClusterStateCollector(
        git_collector=FakeGitCollector(),
        openshift_collector=FakeOpenShiftCollector(),
        tekton_collector=MultipleTektonCollector(),
        cluster_name="example",
    )

    snapshot = collector.collect(
        command_id=UUID(int=1),
        snapshot_id=UUID(int=40),
        collected_at=datetime(2026, 1, 1, tzinfo=UTC),
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
    collector = ClusterStateCollector(
        git_collector=FakeGitCollector(),
        openshift_collector=FakeOpenShiftCollector(),
        tekton_collector=MultipleTektonCollector(),
        cluster_name="example",
        pipeline_run="0171d46a-0000-0000-0000-000000000031",
    )

    snapshot = collector.collect(
        command_id=UUID(int=1),
        snapshot_id=UUID(int=40),
        collected_at=datetime(2026, 1, 1, tzinfo=UTC),
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
    collector = ClusterStateCollector(
        git_collector=FakeGitCollector(),
        openshift_collector=FakeOpenShiftCollector(),
        tekton_collector=MultipleTektonCollector(),
        cluster_name="example",
        pipeline_run="deploy-cluster-older",
    )

    snapshot = collector.collect(
        command_id=UUID(int=1),
        snapshot_id=UUID(int=40),
        collected_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    correlation = snapshot.items[-1]
    assert correlation.data["pipeline_run_evidence_ids"] == [
        str(UUID(int=31))
    ]


def test_cluster_state_collector_reports_missing_selected_run() -> None:
    collector = ClusterStateCollector(
        git_collector=FakeGitCollector(),
        openshift_collector=FakeOpenShiftCollector(),
        tekton_collector=MultipleTektonCollector(),
        cluster_name="example",
        pipeline_run="missing-run",
    )

    snapshot = collector.collect(
        command_id=UUID(int=1),
        snapshot_id=UUID(int=40),
        collected_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    correlation = snapshot.items[-1]
    assert len(snapshot.items) == 3
    assert correlation.data["pipeline_run_evidence_ids"] == []
    assert correlation.data["missing_evidence"] == [
        "Selected Tekton PipelineRun missing-run"
    ]
