from __future__ import annotations

from pathlib import Path
from typing import Any, final

from aura.collectors.oc_cli import (
    CommandRunner,
    OpenShiftCLI,
    OpenShiftCLIResourceClient,
)


@final
class OpenShiftCLIInventoryClient(OpenShiftCLIResourceClient):
    """Read-only OpenShift inventory client backed by the `oc` CLI."""

    def __init__(
        self,
        *,
        kubeconfig_path: Path,
        command_runner: CommandRunner | None = None,
    ) -> None:
        cli = OpenShiftCLI(
            kubeconfig_path=kubeconfig_path,
            command_runner=command_runner,
        )
        super().__init__(
            {
                "clusterdeployments": cli.resource(
                    "clusterdeployments",
                    all_namespaces=True,
                ),
                "managedclusters": cli.resource("managedclusters"),
            }
        )

    def list_cluster_deployments(self) -> list[dict[str, Any]]:
        return self._items("clusterdeployments")

    def list_managed_clusters(self) -> list[dict[str, Any]]:
        return self._items("managedclusters")


__all__ = ["CommandRunner", "OpenShiftCLIInventoryClient"]
