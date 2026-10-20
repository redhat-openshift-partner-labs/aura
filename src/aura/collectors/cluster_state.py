from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol, final
from uuid import UUID, uuid4, uuid5

from aura.collectors.deprovision import (
    DEPROVISION_REQUESTED,
    deprovision_status,
)
from aura.collectors.tekton_inventory import pipeline_run_redaction_prefix
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
        pipeline_run: str | None = None,
    ) -> None:
        self._git_collector = git_collector
        self._openshift_collector = openshift_collector
        self._tekton_collector = tekton_collector
        self._cluster_name = cluster_name
        self._pipeline_run = pipeline_run

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

        git_items = self._cluster_items(
            git_snapshot,
            cluster_name=self._cluster_name,
            kinds={
                "cluster_definition",
                "cluster_definition_incomplete",
                "cluster_definition_invalid",
            },
        )
        sentinel_items = self._cluster_items(
            git_snapshot,
            cluster_name=self._cluster_name,
            kinds={"deprovision_sentinel"},
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
        selected_pipeline_items = self._selected_pipeline_items(
            pipeline_items
        )

        status = deprovision_status(git_items, openshift_item, sentinel_items)
        items: list[EvidenceItem] = []
        items.extend(git_items)
        items.extend(sentinel_items)
        if openshift_item is not None:
            items.append(openshift_item)
        items.extend(selected_pipeline_items)

        missing_evidence = self._missing_evidence(
            git_items=git_items,
            openshift_item=openshift_item,
            pipeline_items=selected_pipeline_items,
            pipeline_run=self._pipeline_run,
            deprovision_requested=status in DEPROVISION_REQUESTED,
        )
        items.append(
            EvidenceItem(
                item_id=uuid5(
                    command_id,
                    f"cluster-state-correlation:{self._cluster_name}",
                ),
                kind="cluster_state_correlation",
                data=_correlation_data(
                    cluster_name=self._cluster_name,
                    namespace=namespace,
                    git_items=git_items,
                    openshift_item=openshift_item,
                    pipeline_items=selected_pipeline_items,
                    sentinel_items=sentinel_items,
                    deprovision_state=status,
                    missing_evidence=missing_evidence,
                ),
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
            redactions=_included_redactions(
                git_snapshot,
                openshift_snapshot,
                tekton_snapshot,
                selected_pipeline_items,
            ),
        )

    @staticmethod
    def _cluster_item(
        snapshot: EvidenceSnapshot,
        *,
        cluster_name: str,
        kinds: set[str],
    ) -> EvidenceItem | None:
        items = ClusterStateCollector._cluster_items(
            snapshot,
            cluster_name=cluster_name,
            kinds=kinds,
        )
        return next(iter(items), None)

    @staticmethod
    def _cluster_items(
        snapshot: EvidenceSnapshot,
        *,
        cluster_name: str,
        kinds: set[str],
    ) -> list[EvidenceItem]:
        return [
            item
            for item in snapshot.items
            if item.kind in kinds
            and item.data.get("cluster_name") == cluster_name
        ]

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

    def _selected_pipeline_items(
        self,
        pipeline_items: list[EvidenceItem],
    ) -> list[EvidenceItem]:
        if self._pipeline_run is None:
            if not pipeline_items:
                return []
            return [
                max(
                    pipeline_items,
                    key=lambda item: str(
                        item.data.get("start_time") or ""
                    ),
                )
            ]
        return [
            item
            for item in pipeline_items
            if self._pipeline_run
            in (
                str(item.data.get("pipeline_run_id") or ""),
                str(item.data.get("pipeline_run_name") or ""),
            )
        ]

    @staticmethod
    def _missing_evidence(
        *,
        git_items: list[EvidenceItem],
        openshift_item: EvidenceItem | None,
        pipeline_items: list[EvidenceItem],
        pipeline_run: str | None,
        deprovision_requested: bool,
    ) -> list[str]:
        missing: list[str] = []
        if len(git_items) > 1:
            missing.append("Conflicting Git cluster definitions")
        elif not git_items:
            missing.append("Git cluster definition")
        elif git_items[0].kind == "cluster_definition_incomplete":
            missing.append("Git cluster definition files")
        elif git_items[0].kind == "cluster_definition_invalid":
            missing.append("Valid Git cluster definition")

        if openshift_item is None:
            if not deprovision_requested:
                missing.append("OpenShift actual state")
        elif openshift_item.kind == "cluster_actual_state_incomplete":
            missing.append("ManagedCluster status")

        if not pipeline_items:
            if pipeline_run is not None:
                missing.append(
                    f"Selected Tekton PipelineRun {pipeline_run}"
                )
                return missing
            missing.append("Tekton PipelineRun status")
        return missing


def _included_redactions(
    git_snapshot: EvidenceSnapshot,
    openshift_snapshot: EvidenceSnapshot,
    tekton_snapshot: EvidenceSnapshot,
    pipeline_items: list[EvidenceItem],
) -> list[str]:
    """Keep source redactions, limiting Tekton ones to selected runs."""

    prefixes = tuple(
        pipeline_run_redaction_prefix(
            str(item.data.get("namespace", "")),
            str(item.data.get("pipeline_run_name", "")),
        )
        + "."
        for item in pipeline_items
    )
    return sorted(
        {
            *git_snapshot.redactions,
            *openshift_snapshot.redactions,
            *(
                redaction
                for redaction in tekton_snapshot.redactions
                if redaction.startswith(prefixes)
            ),
        }
    )


def _evidence_ids(items: list[EvidenceItem]) -> list[str]:
    return [str(item.item_id) for item in items]


def _correlation_data(
    *,
    cluster_name: str,
    namespace: object,
    git_items: list[EvidenceItem],
    openshift_item: EvidenceItem | None,
    pipeline_items: list[EvidenceItem],
    sentinel_items: list[EvidenceItem],
    deprovision_state: str | None,
    missing_evidence: list[str],
) -> dict[str, object]:
    return {
        "cluster_name": cluster_name,
        "namespace": namespace,
        "git_evidence_id": (
            str(git_items[0].item_id) if len(git_items) == 1 else None
        ),
        "git_evidence_ids": _evidence_ids(git_items),
        "definition_conflict": len(git_items) > 1,
        "openshift_evidence_id": (
            str(openshift_item.item_id) if openshift_item is not None else None
        ),
        "pipeline_run_evidence_ids": _evidence_ids(pipeline_items),
        "deprovision_status": deprovision_state,
        "deprovision_sentinel_evidence_ids": _evidence_ids(sentinel_items),
        "missing_evidence": missing_evidence,
    }
