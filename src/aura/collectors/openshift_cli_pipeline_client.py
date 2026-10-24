from __future__ import annotations

from typing import Any, final

from aura.collectors.oc_cli import OpenShiftCLIResourceClient


PIPELINE_NAMESPACE = "openshift-pipelines"


@final
class OpenShiftCLIPipelineClient(OpenShiftCLIResourceClient):
    """Read-only Tekton Pipeline client, separate so other reads need no
    Pipeline RBAC."""

    resource_specs = {"pipelines": {"namespace": PIPELINE_NAMESPACE}}

    def list_pipelines(self) -> list[dict[str, Any]]:
        return self._items("pipelines")


__all__ = ["OpenShiftCLIPipelineClient", "PIPELINE_NAMESPACE"]
