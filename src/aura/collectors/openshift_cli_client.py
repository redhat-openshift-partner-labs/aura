from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, final


CommandRunner = Callable[[Sequence[str]], str]


def _run_command(args: Sequence[str]) -> str:
    result = subprocess.run(
        args,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


@final
class OpenShiftCLIInventoryClient:
    """Read-only OpenShift inventory client backed by the `oc` CLI."""

    def __init__(
        self,
        *,
        kubeconfig_path: Path,
        command_runner: CommandRunner | None = None,
    ) -> None:
        self.kubeconfig_path = kubeconfig_path
        self._command_runner = command_runner or _run_command
        self._cluster_deployments_response: dict[str, Any] | None = None
        self._managed_clusters_response: dict[str, Any] | None = None

    def list_cluster_deployments(self) -> list[dict[str, Any]]:
        response = self._cluster_deployments()
        return response.get("items", [])

    def list_managed_clusters(self) -> list[dict[str, Any]]:
        response = self._managed_clusters()
        return response.get("items", [])

    def resource_version(self) -> str:
        cluster_deployments = self._cluster_deployments()
        managed_clusters = self._managed_clusters()
        cluster_version = cluster_deployments.get("metadata", {}).get(
            "resourceVersion",
            "",
        )
        managed_version = managed_clusters.get("metadata", {}).get(
            "resourceVersion",
            "",
        )
        return (
            f"clusterdeployments={cluster_version};"
            f"managedclusters={managed_version}"
        )

    def _cluster_deployments(self) -> dict[str, Any]:
        if self._cluster_deployments_response is None:
            self._cluster_deployments_response = self._get(
                "clusterdeployments",
                all_namespaces=True,
            )
        return self._cluster_deployments_response

    def _managed_clusters(self) -> dict[str, Any]:
        if self._managed_clusters_response is None:
            self._managed_clusters_response = self._get(
                "managedclusters",
                all_namespaces=False,
            )
        return self._managed_clusters_response

    def _get(
        self,
        resource: str,
        *,
        all_namespaces: bool,
    ) -> dict[str, Any]:
        args = [
            "oc",
            "--kubeconfig",
            str(self.kubeconfig_path),
            "get",
            resource,
        ]
        if all_namespaces:
            args.append("-A")
        args.extend(["-o", "json"])
        return json.loads(self._command_runner(args))


__all__ = ["CommandRunner", "OpenShiftCLIInventoryClient"]
