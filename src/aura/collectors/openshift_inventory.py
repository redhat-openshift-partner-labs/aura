from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol, final
from uuid import UUID, uuid4, uuid5

from aura.contracts import EvidenceItem, EvidenceSnapshot


class OpenShiftInventoryClient(Protocol):
    def list_cluster_deployments(self) -> list[dict[str, Any]]:
        ...

    def list_managed_clusters(self) -> list[dict[str, Any]]:
        ...

    def resource_version(self) -> str:
        ...


@final
class OpenShiftInventoryCollector:
    """Collects actual cluster state from OpenShift and ACM resources."""

    collector_version = "0.1.0"

    def __init__(self, client: OpenShiftInventoryClient) -> None:
        self.client = client

    def collect(
        self,
        *,
        command_id: UUID,
        snapshot_id: UUID | None = None,
        collected_at: datetime | None = None,
        source_version: str | None = None,
    ) -> EvidenceSnapshot:
        resolved_snapshot_id = snapshot_id or uuid4()
        resolved_collected_at = collected_at or datetime.now(UTC)
        resolved_source_version = source_version or self.client.resource_version()
        cluster_deployments = self.client.list_cluster_deployments()
        managed_clusters = {
            resource["metadata"]["name"]: resource
            for resource in self.client.list_managed_clusters()
        }

        items = [
            self._read_cluster(
                resource,
                command_id=command_id,
                source_version=resolved_source_version,
                managed_cluster=managed_clusters.get(
                    resource["metadata"]["name"]
                ),
            )
            for resource in sorted(
                cluster_deployments,
                key=lambda resource: resource["metadata"]["name"],
            )
        ]

        return EvidenceSnapshot(
            snapshot_id=resolved_snapshot_id,
            command_id=command_id,
            collector="openshift_inventory",
            collector_version=self.collector_version,
            collected_at=resolved_collected_at,
            source="openshift",
            source_version=resolved_source_version,
            items=items,
            redactions=[],
        )

    def _read_cluster(
        self,
        resource: dict[str, Any],
        *,
        command_id: UUID,
        source_version: str,
        managed_cluster: dict[str, Any] | None,
    ) -> EvidenceItem:
        metadata = resource.get("metadata", {})
        spec = resource.get("spec", {})
        cluster_name = metadata.get("name", "")
        item_id = uuid5(
            command_id,
            f"{source_version}:cluster_actual_state:{cluster_name}",
        )

        if managed_cluster is None:
            return EvidenceItem(
                item_id=item_id,
                kind="cluster_actual_state_incomplete",
                data={
                    "cluster_name": cluster_name,
                    "missing_managed_cluster": True,
                },
            )

        platform = spec.get("platform", {})
        cloud_provider = next(iter(platform), "")
        provisioning = spec.get("provisioning", {})
        image_set = provisioning.get("imageSetRef", {}).get("name", "")
        managed_metadata = managed_cluster.get("metadata", {})

        return EvidenceItem(
            item_id=item_id,
            kind="cluster_actual_state",
            data={
                "cluster_name": cluster_name,
                "namespace": metadata.get("namespace", ""),
                "cloud_provider": cloud_provider,
                "region": platform.get(cloud_provider, {}).get("region", ""),
                "install_image_set": image_set,
                "cluster_deployment_conditions": self._conditions(resource),
                "managed_cluster_labels": managed_metadata.get("labels", {}),
                "managed_cluster_conditions": self._conditions(
                    managed_cluster
                ),
            },
        )

    @staticmethod
    def _conditions(resource: dict[str, Any]) -> list[dict[str, str]]:
        conditions = resource.get("status", {}).get("conditions", [])
        return [
            {
                "type": condition.get("type", ""),
                "status": condition.get("status", ""),
                "reason": condition.get("reason", ""),
            }
            for condition in conditions
        ]


__all__ = ["OpenShiftInventoryClient", "OpenShiftInventoryCollector"]
