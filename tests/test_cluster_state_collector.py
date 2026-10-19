from __future__ import annotations

from datetime import UTC, datetime
from typing import final
from uuid import UUID

from aura.collectors.cluster_state import ClusterStateCollector
from aura.contracts import EvidenceItem, EvidenceSnapshot


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
