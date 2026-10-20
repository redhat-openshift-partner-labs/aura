from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import final
from uuid import UUID, uuid4, uuid5

from aura.collectors.cluster_state import ClusterStateSourceCollector
from aura.contracts import EvidenceItem, EvidenceSnapshot


@final
@dataclass(frozen=True)
class WorkflowInventoryCollector:
    """Correlates PipelineRuns with clusters and diagnostic next steps."""

    collector_version = "0.1.0"

    openshift_collector: ClusterStateSourceCollector
    tekton_collector: ClusterStateSourceCollector
    git_collector: ClusterStateSourceCollector | None = None

    def collect(
        self,
        *,
        command_id: UUID,
        snapshot_id: UUID | None = None,
        collected_at: datetime | None = None,
        source_version: str | None = None,
    ) -> EvidenceSnapshot:
        resolved_collected_at = collected_at or datetime.now(UTC)
        resolved_snapshot_id = (
            snapshot_id or uuid5(command_id, "workflow-inventory")
        )
        git_snapshot = (
            self.git_collector.collect(
                command_id=command_id,
                collected_at=resolved_collected_at,
            )
            if self.git_collector is not None
            else None
        )
        openshift_snapshot = self.openshift_collector.collect(
            command_id=command_id,
            collected_at=resolved_collected_at,
        )
        tekton_snapshot = self.tekton_collector.collect(
            command_id=command_id,
            collected_at=resolved_collected_at,
        )

        source_snapshots = [
            (name, snapshot)
            for name, snapshot in (
                ("git", git_snapshot),
                ("openshift", openshift_snapshot),
                ("tekton", tekton_snapshot),
            )
            if snapshot is not None
        ]
        items = [
            item
            for _, snapshot in source_snapshots
            for item in snapshot.items
        ]
        items.extend(
            self._workflow_items(
                openshift_snapshot=openshift_snapshot,
                tekton_snapshot=tekton_snapshot,
                command_id=command_id,
            )
        )

        return EvidenceSnapshot(
            snapshot_id=resolved_snapshot_id,
            command_id=command_id,
            collector="workflow_inventory",
            collector_version=self.collector_version,
            collected_at=resolved_collected_at,
            source="+".join(name for name, _ in source_snapshots),
            source_version=(
                source_version
                or ";".join(
                    f"{name}={snapshot.source_version}"
                    for name, snapshot in source_snapshots
                )
            ),
            items=items,
            redactions=sorted(
                {
                    redaction
                    for _, snapshot in source_snapshots
                    for redaction in snapshot.redactions
                }
            ),
        )

    def _workflow_items(
        self,
        *,
        openshift_snapshot: EvidenceSnapshot,
        tekton_snapshot: EvidenceSnapshot,
        command_id: UUID,
    ) -> list[EvidenceItem]:
        clusters_by_namespace = {
            item.data.get("namespace"): item.data.get("cluster_name")
            for item in openshift_snapshot.items
            if item.kind in {
                "cluster_actual_state",
                "cluster_actual_state_incomplete",
            }
        }
        return [
            self._workflow_item(
                pipeline_item,
                clusters_by_namespace=clusters_by_namespace,
                command_id=command_id,
            )
            for pipeline_item in tekton_snapshot.items
            if pipeline_item.kind == "pipeline_run"
        ]

    def _workflow_item(
        self,
        pipeline_item: EvidenceItem,
        *,
        clusters_by_namespace: dict[str | None, str | None],
        command_id: UUID,
    ) -> EvidenceItem:
        namespace = pipeline_item.data.get("namespace")
        related_cluster = pipeline_item.data.get(
            "cluster_name"
        ) or clusters_by_namespace.get(namespace)
        return EvidenceItem(
            item_id=uuid5(
                command_id,
                f"workflow-view:{namespace}/"
                f"{pipeline_item.data.get('pipeline_run_name')}",
            ),
            kind="workflow_view",
            data={
                "pipeline_run_name": pipeline_item.data.get(
                    "pipeline_run_name"
                ),
                "pipeline_name": pipeline_item.data.get("pipeline_name"),
                "status": pipeline_item.data.get("status"),
                "start_time": pipeline_item.data.get("start_time"),
                "completion_time": pipeline_item.data.get(
                    "completion_time"
                ),
                "failed_task": pipeline_item.data.get("failed_task"),
                "related_cluster": related_cluster,
                "next_diagnostic_step": self._next_diagnostic_step(
                    pipeline_item
                ),
            },
        )

    @staticmethod
    def _next_diagnostic_step(
        pipeline_item: EvidenceItem,
    ) -> str:
        failed_task = pipeline_item.data.get("failed_task")
        if failed_task is not None:
            task_name = failed_task.get("name", "unknown")
            return f"Inspect TaskRun {task_name}."
        if pipeline_item.data.get("status") == "Succeeded":
            return "No diagnostic step required."
        return "Inspect PipelineRun conditions and events."


__all__ = ["WorkflowInventoryCollector"]
