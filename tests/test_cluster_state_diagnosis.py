from __future__ import annotations

from datetime import UTC, datetime
from typing import final
from uuid import UUID

from aura.authorization import OperatorRole
from aura.collectors.cluster_state import ClusterStateCollector
from aura.contracts import (
    AuditEvent,
    AuditEventType,
    CommandEnvelope,
    CommandState,
    CommandType,
)
from aura.services.cwa import (
    CWAAssemblyPolicy,
    CWAAssemblyRequest,
    CWAAssembler,
)
from aura.services.cluster_state import ClusterStateDiagnosisModel
from test_cluster_state_collector import (
    FakeGitCollector,
    FakeOpenShiftCollector,
    FakeTektonCollector,
)


def make_snapshot():
    collector = ClusterStateCollector(
        git_collector=FakeGitCollector(),
        openshift_collector=FakeOpenShiftCollector(),
        tekton_collector=FakeTektonCollector(),
        cluster_name="example",
    )
    return collector.collect(
        command_id=UUID(int=1),
        snapshot_id=UUID(int=40),
        collected_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def make_cwa_snapshot(evidence_snapshot):
    command = CommandEnvelope(
        command_id=UUID(int=1),
        command_type=CommandType.DIAGNOSE_STATE,
        operator_id="operator@example.com",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        input={"query": "diagnose cluster state"},
        idempotency_key="test-key",
        status=CommandState.SUCCEEDED,
    )
    audit_event = AuditEvent(
        event_id=UUID(int=20),
        command_id=UUID(int=1),
        event_type=AuditEventType.EVIDENCE_COLLECTED,
        occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
        actor="operator@example.com",
        data={
            "snapshot_id": str(evidence_snapshot.snapshot_id),
            "snapshot_digest": evidence_snapshot.snapshot_digest,
        },
    )
    request = CWAAssemblyRequest(
        command=command,
        evidence_snapshot=evidence_snapshot,
        audit_event=audit_event,
        operator_roles={OperatorRole.VIEWER},
        policy=CWAAssemblyPolicy(
            instructions={"policy": "read-only"},
            capabilities={"adapters": []},
            output_contract={"schema": "diagnosis_state"},
            knowledge={"documents": []},
        ),
        assembled_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    return CWAAssembler().assemble(request).snapshot


def test_cluster_state_diagnosis_model_returns_failed_workflow_answer() -> None:
    snapshot = make_cwa_snapshot(make_snapshot())
    model = ClusterStateDiagnosisModel()

    response = model.diagnose(snapshot)

    assert response.summary == (
        "Cluster example is defined and deployed, but its latest workflow failed."
    )
    assert response.facts[0].observation == (
        "Git defines cluster example in provision/example."
    )
    assert response.facts[1].observation == (
        "OpenShift reports cluster example in namespace example-ns."
    )
    assert response.facts[2].observation == (
        "Tekton PipelineRun deploy-cluster-abc is Failed."
    )
    assert [inference.model_dump() for inference in response.inferences] == [
        {
            "statement": (
                "The failed PipelineRun may explain the current cluster state."
            ),
            "confidence": 0.7,
        }
    ]
    assert response.missing_evidence == []
    assert response.recommended_inspection.description == (
        "Inspect the failed TaskRun provision-task-abc."
    )
    assert response.confidence == 0.8
    assert response.evidence_ids == [
        UUID(int=11),
        UUID(int=21),
        UUID(int=31),
    ]


def test_cluster_state_diagnosis_model_reports_missing_evidence() -> None:
    @final
    class EmptyCollector:
        def collect(
            self,
            *,
            command_id: UUID,
            snapshot_id: UUID | None = None,
            collected_at: datetime | None = None,
            source_version: str | None = None,
        ):
            from aura.contracts import EvidenceSnapshot

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

    collector = ClusterStateCollector(
        git_collector=EmptyCollector(),
        openshift_collector=EmptyCollector(),
        tekton_collector=EmptyCollector(),
        cluster_name="example",
    )
    evidence_snapshot = collector.collect(
        command_id=UUID(int=1),
        snapshot_id=UUID(int=40),
        collected_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    snapshot = make_cwa_snapshot(evidence_snapshot)
    model = ClusterStateDiagnosisModel()

    response = model.diagnose(snapshot)

    assert response.summary == "Cluster example has incomplete evidence."
    assert response.missing_evidence == [
        "Git cluster definition",
        "OpenShift actual state",
        "Tekton PipelineRun status",
    ]
    assert response.recommended_inspection.description == (
        "Inspect the Git, OpenShift, and Tekton evidence for example."
    )
    assert response.confidence == 0.4
