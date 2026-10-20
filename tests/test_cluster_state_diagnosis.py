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
    EvidenceSnapshot,
)
from aura.services.cwa import (
    CWAAssemblyPolicy,
    CWAAssemblyRequest,
    CWAAssembler,
)
import pytest

from aura.diagnosis import DiagnosisEvidenceUnavailable
from aura.services.cluster_state import ClusterStateDiagnosisModel
from test_cluster_state_collector import (
    ConflictingGitCollector,
    collect_cluster_state,
    ConditionOpenShiftCollector,
    EmptyCollector,
    FakeGitCollector,
    FakeOpenShiftCollector,
    FakeTektonCollector,
    IncompleteGitCollector,
)


def make_snapshot(*, complete: bool = True):
    if complete:
        git_collector = FakeGitCollector()
        openshift_collector = FakeOpenShiftCollector()
        tekton_collector = FakeTektonCollector()
    else:
        git_collector = EmptyCollector()
        openshift_collector = EmptyCollector()
        tekton_collector = EmptyCollector()
    collector = ClusterStateCollector(
        git_collector=git_collector,
        openshift_collector=openshift_collector,
        tekton_collector=tekton_collector,
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
        "Cluster example is defined and deployed, but PipelineRun "
        "deploy-cluster-abc failed."
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
    evidence_snapshot = make_snapshot(complete=False)
    snapshot = make_cwa_snapshot(evidence_snapshot)
    model = ClusterStateDiagnosisModel()

    with pytest.raises(DiagnosisEvidenceUnavailable) as error:
        model.diagnose(snapshot)

    assert error.value.missing_evidence == [
        "Git cluster definition",
        "OpenShift actual state",
        "Tekton PipelineRun status",
    ]


def test_cluster_state_diagnosis_model_explains_not_provisioned() -> None:
    evidence_snapshot = collect_cluster_state(
        git_collector=FakeGitCollector(),
        openshift_collector=ConditionOpenShiftCollector(
            {
                "type": "Provisioned",
                "status": "False",
                "reason": "ProvisioningFailed",
            }
        ),
        tekton_collector=FakeTektonCollector(),
    )
    snapshot = make_cwa_snapshot(evidence_snapshot)

    response = ClusterStateDiagnosisModel().diagnose(snapshot)

    assert response.summary == (
        "Cluster example is defined but not provisioned, and "
        "PipelineRun deploy-cluster-abc failed."
    )
    assert response.facts[1].observation == (
        "OpenShift reports cluster example in namespace example-ns; "
        "its Provisioned condition is False with reason "
        "ProvisioningFailed."
    )
    assert [inference.model_dump() for inference in response.inferences] == [
        {
            "statement": (
                "The failed PipelineRun may explain why cluster example "
                "is not provisioned."
            ),
            "confidence": 0.7,
        }
    ]
    assert response.recommended_inspection.description == (
        "Read-only inspect the failed TaskRun provision-task-abc and "
        "the OpenShift Provisioned condition."
    )


INCOMPLETE_RUN_INFERENCE = {
    "statement": (
        "PipelineRun deploy-cluster-abc has not completed, so the "
        "cluster state may still change."
    ),
    "confidence": 0.6,
}


@pytest.mark.parametrize(
    (
        "status",
        "provisioned",
        "summary",
        "inferences",
        "inspection",
        "confidence",
    ),
    [
        (
            "Unknown",
            True,
            "Cluster example is defined and deployed, and PipelineRun "
            "deploy-cluster-abc has not completed.",
            [INCOMPLETE_RUN_INFERENCE],
            "Inspect the conditions and TaskRuns of incomplete "
            "PipelineRun deploy-cluster-abc.",
            0.6,
        ),
        (
            "Unknown",
            False,
            "Cluster example is defined but not provisioned, and "
            "PipelineRun deploy-cluster-abc has not completed.",
            [INCOMPLETE_RUN_INFERENCE],
            "Read-only inspect the conditions and TaskRuns of incomplete "
            "PipelineRun deploy-cluster-abc and the OpenShift "
            "Provisioned condition.",
            0.6,
        ),
        (
            "Succeeded",
            True,
            "Cluster example is defined and deployed, and PipelineRun "
            "deploy-cluster-abc succeeded.",
            [],
            "Review the cluster's current state and recent workflow.",
            0.9,
        ),
        (
            "Succeeded",
            False,
            "Cluster example is defined but not provisioned, and "
            "PipelineRun deploy-cluster-abc succeeded.",
            [],
            "Read-only inspect the OpenShift Provisioned condition and "
            "the cluster's recent workflow.",
            0.9,
        ),
    ],
)
def test_cluster_state_diagnosis_model_reports_pipeline_run_status(
    status,
    provisioned,
    summary,
    inferences,
    inspection,
    confidence,
) -> None:
    evidence_snapshot = collect_cluster_state(
        git_collector=FakeGitCollector(),
        openshift_collector=ConditionOpenShiftCollector(
            {
                "type": "Provisioned",
                "status": "True" if provisioned else "False",
                "reason": "" if provisioned else "ProvisioningFailed",
            }
        ),
        tekton_collector=FakeTektonCollector(status),
    )

    response = ClusterStateDiagnosisModel().diagnose(
        make_cwa_snapshot(evidence_snapshot)
    )

    assert response.summary == summary
    assert [
        inference.model_dump() for inference in response.inferences
    ] == inferences
    assert response.recommended_inspection.description == inspection
    assert response.confidence == confidence


def test_cluster_state_diagnosis_model_requires_provisioned_condition() -> None:
    evidence_snapshot = collect_cluster_state(
        git_collector=FakeGitCollector(),
        openshift_collector=ConditionOpenShiftCollector(None),
        tekton_collector=FakeTektonCollector(),
    )
    snapshot = make_cwa_snapshot(evidence_snapshot)

    with pytest.raises(DiagnosisEvidenceUnavailable) as error:
        ClusterStateDiagnosisModel().diagnose(snapshot)

    assert error.value.missing_evidence == [
        "OpenShift Provisioned condition"
    ]


def test_cluster_state_diagnosis_model_refuses_definition_conflict() -> None:
    evidence_snapshot = collect_cluster_state(
        git_collector=ConflictingGitCollector(),
        openshift_collector=FakeOpenShiftCollector(),
        tekton_collector=FakeTektonCollector(),
    )
    snapshot = make_cwa_snapshot(evidence_snapshot)

    with pytest.raises(DiagnosisEvidenceUnavailable) as error:
        ClusterStateDiagnosisModel().diagnose(snapshot)

    assert error.value.missing_evidence == [
        "Conflicting Git cluster definitions"
    ]


def test_cluster_state_diagnosis_model_refuses_incomplete_definition() -> None:
    evidence_snapshot = collect_cluster_state(
        git_collector=IncompleteGitCollector(),
        openshift_collector=FakeOpenShiftCollector(),
        tekton_collector=FakeTektonCollector(),
    )
    snapshot = make_cwa_snapshot(evidence_snapshot)

    with pytest.raises(DiagnosisEvidenceUnavailable) as error:
        ClusterStateDiagnosisModel().diagnose(snapshot)

    assert error.value.missing_evidence == [
        "Git cluster definition files"
    ]


def test_cluster_state_diagnosis_model_refuses_missing_correlation() -> None:
    evidence_snapshot = EvidenceSnapshot(
        snapshot_id=UUID(int=40),
        command_id=UUID(int=1),
        collector="empty",
        collector_version="test",
        collected_at=datetime(2026, 1, 1, tzinfo=UTC),
        source="empty",
        source_version="test",
        items=[],
        redactions=[],
    )
    snapshot = make_cwa_snapshot(evidence_snapshot)

    with pytest.raises(DiagnosisEvidenceUnavailable) as error:
        ClusterStateDiagnosisModel().diagnose(snapshot)

    assert error.value.missing_evidence == ["Cluster state correlation"]
