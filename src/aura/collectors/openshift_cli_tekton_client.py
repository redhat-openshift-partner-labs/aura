from __future__ import annotations

from pathlib import Path
from typing import Any, final

from aura.collectors.oc_cli import (
    CommandRunner,
    OpenShiftCLI,
    OpenShiftCLIResourceClient,
)


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
        cli = OpenShiftCLI(
            kubeconfig_path=kubeconfig_path,
            command_runner=command_runner,
        )
        all_namespaces = namespace is None
        super().__init__(
            {
                "pipelineruns": cli.resource(
                    "pipelineruns",
                    namespace=namespace,
                    all_namespaces=all_namespaces,
                ),
                "taskruns": cli.resource(
                    "taskruns",
                    namespace=namespace,
                    all_namespaces=all_namespaces,
                ),
            }
        )

    def list_pipeline_runs(self) -> list[dict[str, Any]]:
        return self._items("pipelineruns")

    def list_task_runs(self) -> list[dict[str, Any]]:
        return self._items("taskruns")


__all__ = ["CommandRunner", "OpenShiftCLITektonClient"]
