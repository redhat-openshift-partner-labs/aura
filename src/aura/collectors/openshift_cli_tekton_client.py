from __future__ import annotations

from pathlib import Path
from typing import Any, final

from aura.collectors.oc_cli import CommandRunner, OpenShiftCLIResourceClient


@final
class OpenShiftCLITektonClient(OpenShiftCLIResourceClient):
    """Read-only Tekton client backed by the `oc` CLI."""

    def __init__(
        self,
        *,
        kubeconfig_path: Path,
        namespace: str | None = None,
        command_runner: CommandRunner | None = None,
    ) -> None:
        scope = {
            "namespace": namespace,
            "all_namespaces": namespace is None,
        }
        self.resource_specs = {"pipelineruns": scope, "taskruns": scope}
        super().__init__(
            kubeconfig_path=kubeconfig_path,
            command_runner=command_runner,
        )

    def list_pipeline_runs(self) -> list[dict[str, Any]]:
        return self._items("pipelineruns")

    def list_task_runs(self) -> list[dict[str, Any]]:
        return self._items("taskruns")


__all__ = ["CommandRunner", "OpenShiftCLITektonClient"]
