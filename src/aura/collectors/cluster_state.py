from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol, final
from uuid import UUID, uuid4, uuid5

from aura.contracts import EvidenceItem, EvidenceSnapshot


class ClusterStateSourceCollector(Protocol):
    def collect(
        self,
        *,
        command_id: UUID,
        snapshot_id: UUID | None = None,
        collected_at: datetime | None = None,
        source_version: str | None = None,
    ) -> EvidenceSnapshot:
        ...


@final
class ClusterStateCollector:
    """Correlates Git, OpenShift, and Tekton evidence for one cluster."""

    collector_version = "0.1.0"

    def __init__(
        self,
        *,
        git_collector: ClusterStateSourceCollector,
        openshift_collector: ClusterStateSourceCollector,
        tekton_collector: ClusterStateSourceCollector,
        cluster_name: str,
    ) -> None:
        self._git_collector = git_collector
        self._openshift_collector = openshift_collector
        self._tekton_collector = tekton_collector
        self._cluster_name = cluster_name

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
            snapshot_id
            or uuid5(command_id, f"cluster-state:{self._cluster_name}")
        )

        git_snapshot = self._git_collector.collect(
            command_id=command_id,
            collected_at=resolved_collected_at,
        )
        openshift_snapshot = self._openshift_collector.collect(
            command_id=command_id,
            collected_at=resolved_collected_at,
        )
        tekton_snapshot = self._tekton_collector.collect(
            command_id=command_id,
            collected_at=resolved_collected_at,
        )

        git_item = self._cluster_item(
            git_snapshot,
            cluster_name=self._cluster_name,
            kinds={"cluster_definition"},
        )
        openshift_item = self._cluster_item(
            openshift_snapshot,
            cluster_name=self._cluster_name,
            kinds={
                "cluster_actual_state",
                "cluster_actual_state_incomplete",
            },
        )
        namespace = (
            openshift_item.data.get("namespace")
            if openshift_item is not None
            else None
        )
        pipeline_items = self._pipeline_items(
            tekton_snapshot,
            cluster_name=self._cluster_name,
            namespace=namespace,
        )

        items: list[EvidenceItem] = []
        if git_item is not None:
            items.append(git_item)
        if openshift_item is not None:
            items.append(openshift_item)
        items.extend(pipeline_items)

        missing_evidence = self._missing_evidence(
            git_item=git_item,
            openshift_item=openshift_item,
            pipeline_items=pipeline_items,
        )
        items.append(
            EvidenceItem(
                item_id=uuid5(
                    command_id,
                    f"cluster-state-correlation:{self._cluster_name}",
                ),
                kind="cluster_state_correlation",
                data={
                    "cluster_name": self._cluster_name,
                    "namespace": namespace,
                    "git_evidence_id": (
                        str(git_item.item_id)
                        if git_item is not None
                        else None
                    ),
                    "openshift_evidence_id": (
                        str(openshift_item.item_id)
                        if openshift_item is not None
                        else None
                    ),
                    "pipeline_run_evidence_ids": [
                        str(item.item_id) for item in pipeline_items
                    ],
                    "missing_evidence": missing_evidence,
                },
            )
        )

        return EvidenceSnapshot(
            snapshot_id=resolved_snapshot_id,
            command_id=command_id,
            collector="cluster_state",
            collector_version=self.collector_version,
            collected_at=resolved_collected_at,
            source="git+openshift+tekton",
            source_version=(
                source_version
                or (
                    f"git={git_snapshot.source_version};"
                    f"openshift={openshift_snapshot.source_version};"
                    f"tekton={tekton_snapshot.source_version}"
                )
            ),
            items=items,
            redactions=[],
        )

    @staticmethod
    def _cluster_item(
        snapshot: EvidenceSnapshot,
        *,
        cluster_name: str,
        kinds: set[str],
    ) -> EvidenceItem | None:
        return next(
            (
                item
                for item in snapshot.items
                if item.kind in kinds
                and item.data.get("cluster_name") == cluster_name
            ),
            None,
        )

    @staticmethod
    def _pipeline_items(
        snapshot: EvidenceSnapshot,
        *,
        cluster_name: str,
        namespace: str | None,
    ) -> list[EvidenceItem]:
        return [
            item
            for item in snapshot.items
            if item.kind == "pipeline_run"
            and (
                item.data.get("cluster_name") == cluster_name
                or (
                    namespace is not None
                    and item.data.get("namespace") == namespace
                )
            )
        ]

    @staticmethod
    def _missing_evidence(
        *,
        git_item: EvidenceItem | None,
        openshift_item: EvidenceItem | None,
        pipeline_items: list[EvidenceItem],
    ) -> list[str]:
        missing: list[str] = []
        if git_item is None:
            missing.append("Git cluster definition")
        elif git_item.kind == "cluster_definition_incomplete":
            missing.append("Git cluster definition files")

        if openshift_item is None:
            missing.append("OpenShift actual state")
        elif openshift_item.kind == "cluster_actual_state_incomplete":
            missing.append("ManagedCluster status")

        if not pipeline_items:
            missing.append("Tekton PipelineRun status")
        return missing
