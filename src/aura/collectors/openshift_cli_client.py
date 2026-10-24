from __future__ import annotations

from typing import Any, final

from aura.collectors.oc_cli import CommandRunner, OpenShiftCLIResourceClient


@final
class OpenShiftCLIInventoryClient(OpenShiftCLIResourceClient):
    """Read-only OpenShift inventory client backed by the `oc` CLI."""

    resource_specs = {
        "clusterdeployments": {"all_namespaces": True},
        "managedclusters": {},
    }

    def list_cluster_deployments(self) -> list[dict[str, Any]]:
        return self._items("clusterdeployments")

    def list_managed_clusters(self) -> list[dict[str, Any]]:
        return self._items("managedclusters")


__all__ = ["CommandRunner", "OpenShiftCLIInventoryClient"]
