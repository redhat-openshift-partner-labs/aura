from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import final
from uuid import UUID, uuid4, uuid5

from aura.collectors.cluster_state import ClusterStateSourceCollector
from aura.contracts import EvidenceItem, EvidenceSnapshot


@final
@dataclass(frozen=True)
class ClusterInventoryCollector:
    """Correlates Git, OpenShift, and Tekton evidence for all clusters."""

    collector_version = "0.1.0"

    git_collector: ClusterStateSourceCollector
    openshift_collector: ClusterStateSourceCollector
    tekton_collector: ClusterStateSourceCollector

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
            snapshot_id or uuid5(command_id, "cluster-inventory")
        )
        git_snapshot = self.git_collector.collect(
            command_id=command_id,
            collected_at=resolved_collected_at,
        )
        openshift_snapshot = self.openshift_collector.collect(
            command_id=command_id,
            collected_at=resolved_collected_at,
        )
        tekton_snapshot = self.tekton_collector.collect(
            command_id=command_id,
            collected_at=resolved_collected_at,
        )

        items = [
            *git_snapshot.items,
            *openshift_snapshot.items,
            *tekton_snapshot.items,
        ]
        items.extend(
            self._inventory_items(
                git_snapshot=git_snapshot,
                openshift_snapshot=openshift_snapshot,
                tekton_snapshot=tekton_snapshot,
                command_id=command_id,
            )
        )

        return EvidenceSnapshot(
            snapshot_id=resolved_snapshot_id,
            command_id=command_id,
            collector="cluster_inventory",
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

    def _inventory_items(
        self,
        *,
        git_snapshot: EvidenceSnapshot,
        openshift_snapshot: EvidenceSnapshot,
        tekton_snapshot: EvidenceSnapshot,
        command_id: UUID,
    ) -> list[EvidenceItem]:
        git_items = self._grouped_items_by_cluster(
            git_snapshot,
            kinds={
                "cluster_definition",
                "cluster_definition_incomplete",
                "cluster_definition_invalid",
            },
        )
        openshift_items = self._items_by_cluster(
            openshift_snapshot,
            kinds={
                "cluster_actual_state",
                "cluster_actual_state_incomplete",
            },
        )
        pipeline_items = self._pipeline_items_by_cluster(
            tekton_snapshot,
            openshift_items=openshift_items,
        )
        cluster_names = sorted(
            set(git_items) | set(openshift_items)
        )

        return [
            self._inventory_item(
                cluster_name=cluster_name,
                git_items=git_items.get(cluster_name, []),
                openshift_item=openshift_items.get(cluster_name),
                pipeline_items=pipeline_items.get(cluster_name, []),
                command_id=command_id,
            )
            for cluster_name in cluster_names
        ]

    def _inventory_item(
        self,
        *,
        cluster_name: str,
        git_items: list[EvidenceItem],
        openshift_item: EvidenceItem | None,
        pipeline_items: list[EvidenceItem],
        command_id: UUID,
    ) -> EvidenceItem:
        definition_conflict = len(git_items) > 1
        definition_statuses = sorted(
            {
                str(item.data.get("status"))
                for item in git_items
            }
        )
        git_item = git_items[0] if len(git_items) == 1 else None
        desired_state = self._desired_state(git_item)
        actual_state = self._actual_state(openshift_item)
        display_state = desired_state or actual_state or {}
        return EvidenceItem(
            item_id=uuid5(
                command_id,
                f"cluster-inventory:{cluster_name}",
            ),
            kind="cluster_inventory",
            data={
                "cluster_name": cluster_name,
                "definition_status": (
                    "conflict"
                    if definition_conflict
                    else (
                        git_item.data.get("status") if git_item else None
                    )
                ),
                "definition_statuses": definition_statuses,
                "definition_conflict": definition_conflict,
                "definition_kind": git_item.kind if git_item else None,
                "tier": display_state.get("tier"),
                "cloud_provider": display_state.get("cloud_provider"),
                "region": display_state.get("region"),
                "desired_state": desired_state,
                "actual_state": actual_state,
                "provisioning_status": self._provisioning_status(
                    openshift_item
                ),
                "latest_pipeline_run": self._latest_pipeline_run(
                    pipeline_items
                ),
                "drift_indicator": self._drift_indicator(
                    git_item=git_item,
                    openshift_item=openshift_item,
                    desired_state=desired_state,
                    actual_state=actual_state,
                    definition_conflict=definition_conflict,
                ),
            },
        )

    @staticmethod
    def _grouped_items_by_cluster(
        snapshot: EvidenceSnapshot,
        *,
        kinds: set[str],
    ) -> dict[str, list[EvidenceItem]]:
        result: dict[str, list[EvidenceItem]] = {}
        for item in snapshot.items:
            if item.kind in kinds:
                result.setdefault(
                    item.data["cluster_name"],
                    [],
                ).append(item)
        return result

    @staticmethod
    def _items_by_cluster(
        snapshot: EvidenceSnapshot,
        *,
        kinds: set[str],
    ) -> dict[str, EvidenceItem]:
        return {
            item.data["cluster_name"]: item
            for item in snapshot.items
            if item.kind in kinds
        }

    @staticmethod
    def _pipeline_items_by_cluster(
        tekton_snapshot: EvidenceSnapshot,
        *,
        openshift_items: dict[str, EvidenceItem],
    ) -> dict[str, list[EvidenceItem]]:
        namespaces = {
            item.data.get("namespace"): cluster_name
            for cluster_name, item in openshift_items.items()
        }
        result: dict[str, list[EvidenceItem]] = {}
        for item in tekton_snapshot.items:
            if item.kind != "pipeline_run":
                continue
            cluster_name = item.data.get(
                "cluster_name"
            ) or namespaces.get(item.data.get("namespace"))
            if cluster_name is not None:
                result.setdefault(cluster_name, []).append(item)
        return result

    @staticmethod
    def _state(
        item: EvidenceItem | None,
        *,
        kind: str,
        tier: object,
    ) -> dict[str, object] | None:
        if item is None or item.kind != kind:
            return None
        return {
            "cloud_provider": item.data.get("cloud_provider"),
            "region": item.data.get("region"),
            "tier": tier,
            "install_image_set": item.data.get("install_image_set"),
        }

    @staticmethod
    def _desired_state(
        git_item: EvidenceItem | None,
    ) -> dict[str, object] | None:
        return ClusterInventoryCollector._state(
            git_item,
            kind="cluster_definition",
            tier=git_item.data.get("tier") if git_item else None,
        )

    @staticmethod
    def _actual_state(
        openshift_item: EvidenceItem | None,
    ) -> dict[str, object] | None:
        labels = (
            openshift_item.data.get("managed_cluster_labels", {})
            if openshift_item is not None
            else {}
        )
        return ClusterInventoryCollector._state(
            openshift_item,
            kind="cluster_actual_state",
            tier=labels.get("tier"),
        )

    @staticmethod
    def _provisioning_status(
        openshift_item: EvidenceItem | None,
    ) -> str | None:
        if openshift_item is None:
            return None
        conditions = openshift_item.data.get(
            "cluster_deployment_conditions",
            [],
        )
        condition = next(
            (
                entry
                for entry in conditions
                if entry.get("type") == "Provisioned"
            ),
            {},
        )
        return {
            "True": "Provisioned",
            "False": "NotProvisioned",
        }.get(condition.get("status"), "Unknown")

    @staticmethod
    def _latest_pipeline_run(
        pipeline_items: list[EvidenceItem],
    ) -> dict[str, object] | None:
        if not pipeline_items:
            return None
        latest = max(
            pipeline_items,
            key=lambda item: str(item.data.get("start_time") or ""),
        )
        fields = (
            "pipeline_run_name",
            "pipeline_name",
            "status",
            "start_time",
            "completion_time",
            "failed_task",
        )
        return {field: latest.data.get(field) for field in fields}

    @staticmethod
    def _drift_indicator(
        *,
        git_item: EvidenceItem | None,
        openshift_item: EvidenceItem | None,
        desired_state: dict[str, object] | None,
        actual_state: dict[str, object] | None,
        definition_conflict: bool,
    ) -> str:
        if definition_conflict:
            return "definition-conflict"
        if git_item is None:
            return "missing-git"
        if git_item.data.get("status") == "archived":
            return (
                "archived-with-openshift-resources"
                if openshift_item is not None
                else "archived"
            )
        if openshift_item is None:
            return "missing-openshift"
        if git_item.kind == "cluster_definition_incomplete":
            return "incomplete-definition"
        if git_item.kind == "cluster_definition_invalid":
            return "invalid-definition"
        if desired_state != actual_state:
            return "detected"
        return "none"

__all__ = ["ClusterInventoryCollector"]
