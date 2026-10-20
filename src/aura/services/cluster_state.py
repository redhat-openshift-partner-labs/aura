from __future__ import annotations

from uuid import UUID
from typing import final

from aura.authorization import OperatorRole
from aura.contracts import CommandEnvelope, CommandType, EvidenceItem
from aura.cwa import CWASnapshot
from aura.diagnosis import (
    DiagnosisEvidenceUnavailable,
    DiagnosisFact,
    DiagnosisInference,
    DiagnosisResponse,
    RecommendedInspection,
)
from aura.services.evidence import (
    EvidenceCollector,
    EvidenceCommandResult,
    EvidenceCommandService,
    make_evidence_command_spec,
)
from aura.services.diagnosis import cwa_evidence_items


class UnauthorizedClusterStateCommand(Exception):
    """Raised when an operator cannot diagnose cluster state."""


class InvalidClusterStateCommand(Exception):
    """Raised when a command is not a cluster-state diagnosis command."""


ClusterStateResult = EvidenceCommandResult


@final
class ClusterStateDiagnosisModel:
    """Deterministic diagnosis model for correlated cluster state."""

    provider_name = "aura"
    model_name = "cluster-state-deterministic"

    def diagnose(self, snapshot: CWASnapshot) -> DiagnosisResponse:
        snapshot_items = cwa_evidence_items(snapshot)
        correlation = next(
            (
                item
                for item in snapshot_items
                if item.kind == "cluster_state_correlation"
            ),
            None,
        )
        if correlation is None:
            raise DiagnosisEvidenceUnavailable(
                ["Cluster state correlation"]
            )

        items_by_id = {item.item_id: item for item in snapshot_items}
        git_item = self._referenced_item(
            correlation,
            items_by_id,
            "git_evidence_id",
        )
        openshift_item = self._referenced_item(
            correlation,
            items_by_id,
            "openshift_evidence_id",
        )
        pipeline_items = [
            items_by_id[UUID(value)]
            for value in correlation.data.get(
                "pipeline_run_evidence_ids",
                [],
            )
            if UUID(value) in items_by_id
        ]
        missing_evidence = list(
            correlation.data.get("missing_evidence", []),
        )
        provisioned_condition = self._provisioned_condition(
            openshift_item
        )
        if (
            openshift_item is not None
            and not provisioned_condition.get("status", "")
        ):
            missing_evidence.append("OpenShift Provisioned condition")
        if missing_evidence:
            raise DiagnosisEvidenceUnavailable(missing_evidence)

        provisioned = (
            provisioned_condition.get("status") == "True"
        )

        facts = self._facts(
            git_item=git_item,
            openshift_item=openshift_item,
            pipeline_items=pipeline_items,
            correlation=correlation,
            provisioned_condition=provisioned_condition,
        )
        failed_pipeline = next(
            (
                item
                for item in pipeline_items
                if item.data.get("status") == "Failed"
            ),
            None,
        )
        inferences = self._inferences(
            failed_pipeline,
            cluster_name=str(correlation.data["cluster_name"]),
            provisioned=provisioned,
        )
        recommended_inspection = self._recommended_inspection(
            cluster_name=str(correlation.data["cluster_name"]),
            failed_pipeline=failed_pipeline,
            provisioned=provisioned,
        )
        summary = self._summary(
            cluster_name=str(correlation.data["cluster_name"]),
            failed_pipeline=failed_pipeline,
            provisioned=provisioned,
        )
        confidence = (
            0.8
            if failed_pipeline is not None
            else 0.9
        )

        return DiagnosisResponse(
            summary=summary,
            facts=facts,
            inferences=inferences,
            missing_evidence=[],
            recommended_inspection=recommended_inspection,
            confidence=confidence,
            evidence_ids=[fact.evidence_id for fact in facts],
        )

    @staticmethod
    def _referenced_item(
        correlation: EvidenceItem,
        items_by_id: dict,
        field: str,
    ) -> EvidenceItem | None:
        value = correlation.data.get(field)
        if value is None:
            return None
        return items_by_id.get(UUID(value))

    @staticmethod
    def _provisioned_condition(
        openshift_item: EvidenceItem | None,
    ) -> dict:
        if openshift_item is None:
            return {}
        conditions = openshift_item.data.get(
            "cluster_deployment_conditions",
            [],
        )
        return next(
            (
                condition
                for condition in conditions
                if condition.get("type") == "Provisioned"
            ),
            {},
        )

    @staticmethod
    def _facts(
        *,
        git_item: EvidenceItem | None,
        openshift_item: EvidenceItem | None,
        pipeline_items: list[EvidenceItem],
        correlation: EvidenceItem,
        provisioned_condition: dict,
    ) -> list[DiagnosisFact]:
        facts: list[DiagnosisFact] = []
        if git_item is not None:
            facts.append(
                DiagnosisFact(
                    evidence_id=git_item.item_id,
                    observation=(
                        f"Git defines cluster {git_item.data['cluster_name']} "
                        f"in {git_item.data['path']}."
                    ),
                )
            )
        if openshift_item is not None:
            observation = (
                f"OpenShift reports cluster "
                f"{openshift_item.data['cluster_name']} in namespace "
                f"{openshift_item.data['namespace']}"
            )
            if provisioned_condition.get("status") != "True":
                reason = (
                    provisioned_condition.get("reason")
                    or "unspecified"
                )
                observation += (
                    f"; its Provisioned condition is "
                    f"{provisioned_condition.get('status')} "
                    f"with reason {reason}."
                )
            else:
                observation += "."
            facts.append(
                DiagnosisFact(
                    evidence_id=openshift_item.item_id,
                    observation=observation,
                )
            )
        for pipeline_item in pipeline_items:
            facts.append(
                DiagnosisFact(
                    evidence_id=pipeline_item.item_id,
                    observation=(
                        f"Tekton PipelineRun "
                        f"{pipeline_item.data['pipeline_run_name']} is "
                        f"{pipeline_item.data['status']}."
                    ),
                )
            )
        if not facts:
            facts.append(
                DiagnosisFact(
                    evidence_id=correlation.item_id,
                    observation="Cluster state correlation is incomplete.",
                )
            )
        return facts

    @staticmethod
    def _inferences(
        failed_pipeline: EvidenceItem | None,
        *,
        cluster_name: str,
        provisioned: bool,
    ) -> list[DiagnosisInference]:
        if failed_pipeline is None:
            return []
        statement = (
            "The failed PipelineRun may explain the current cluster state."
            if provisioned
            else (
                "The failed PipelineRun may explain why cluster "
                f"{cluster_name} is not provisioned."
            )
        )
        return [
            DiagnosisInference(
                statement=statement,
                confidence=0.7,
            )
        ]

    @staticmethod
    def _recommended_inspection(
        *,
        cluster_name: str,
        failed_pipeline: EvidenceItem | None,
        provisioned: bool,
    ) -> RecommendedInspection:
        if failed_pipeline is not None:
            failed_task = failed_pipeline.data.get("failed_task") or {}
            task_name = failed_task.get("name", "the failed task")
            if provisioned:
                description = f"Inspect the failed TaskRun {task_name}."
            else:
                description = (
                    f"Read-only inspect the failed TaskRun {task_name} "
                    "and the OpenShift Provisioned condition."
                )
        else:
            if provisioned:
                description = (
                    "Review the cluster's current state and recent workflow."
                )
            else:
                description = (
                    "Read-only inspect the OpenShift Provisioned condition "
                    "and the cluster's recent workflow."
                )
        return RecommendedInspection(description=description)

    @staticmethod
    def _summary(
        *,
        cluster_name: str,
        failed_pipeline: EvidenceItem | None,
        provisioned: bool,
    ) -> str:
        if not provisioned:
            workflow = (
                ", and its latest workflow failed."
                if failed_pipeline is not None
                else "."
            )
            return (
                f"Cluster {cluster_name} is defined but not provisioned"
                f"{workflow}"
            )
        if failed_pipeline is not None:
            return (
                f"Cluster {cluster_name} is defined and deployed, but its "
                "latest workflow failed."
            )
        return (
            f"Cluster {cluster_name} is defined and deployed, and its "
            "latest workflow succeeded."
        )


class ClusterStateService(EvidenceCommandService):
    """Runs read-only cluster-state diagnosis for an accepted command."""

    spec = make_evidence_command_spec(
        command_type=CommandType.DIAGNOSE_STATE,
        unauthorized_error=UnauthorizedClusterStateCommand,
        invalid_error=InvalidClusterStateCommand,
        action="diagnose cluster state",
    )

    def diagnose_state(
        self,
        command: CommandEnvelope,
        operator_roles: set[OperatorRole],
    ) -> ClusterStateResult:
        return self.run(command, operator_roles)


__all__ = [
    "ClusterStateDiagnosisModel",
    "ClusterStateResult",
    "ClusterStateService",
    "InvalidClusterStateCommand",
    "UnauthorizedClusterStateCommand",
]
