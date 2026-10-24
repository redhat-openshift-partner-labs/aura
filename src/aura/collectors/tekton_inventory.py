from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol, final
from uuid import UUID, uuid4, uuid5

from aura.contracts import EvidenceItem, EvidenceSnapshot
from aura.redaction import redact_parameters


class TektonInventoryClient(Protocol):
    def list_pipeline_runs(self) -> list[dict[str, Any]]:
        ...

    def list_task_runs(self) -> list[dict[str, Any]]:
        ...

    def resource_version(self) -> str:
        ...


@final
class TektonInventoryCollector:
    """Collects PipelineRun and TaskRun status from Tekton."""

    collector_version = "0.1.0"

    def __init__(self, client: TektonInventoryClient) -> None:
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
        pipeline_runs = self.client.list_pipeline_runs()
        task_runs = self.client.list_task_runs()
        task_runs_by_pipeline = self._task_runs_by_pipeline(task_runs)
        redactions: list[str] = []

        items = [
            self._read_pipeline_run(
                resource,
                command_id=command_id,
                source_version=resolved_source_version,
                task_runs=task_runs_by_pipeline.get(
                    (
                        resource.get("metadata", {}).get("namespace", ""),
                        resource.get("metadata", {}).get("name", ""),
                    ),
                    [],
                ),
                redactions=redactions,
            )
            for resource in sorted(
                pipeline_runs,
                key=lambda resource: (
                    resource.get("metadata", {}).get("namespace", ""),
                    resource.get("metadata", {}).get("name", ""),
                ),
            )
        ]

        return EvidenceSnapshot(
            snapshot_id=resolved_snapshot_id,
            command_id=command_id,
            collector="tekton_inventory",
            collector_version=self.collector_version,
            collected_at=resolved_collected_at,
            source="tekton",
            source_version=resolved_source_version,
            items=items,
            redactions=sorted(redactions),
        )

    def _read_pipeline_run(
        self,
        resource: dict[str, Any],
        *,
        command_id: UUID,
        source_version: str,
        task_runs: list[dict[str, Any]],
        redactions: list[str],
    ) -> EvidenceItem:
        metadata = resource.get("metadata", {})
        spec = resource.get("spec", {})
        status = resource.get("status", {})
        namespace = metadata.get("namespace", "")
        name = metadata.get("name", "")
        item_id = uuid5(
            command_id,
            f"{source_version}:pipeline_run:{namespace}/{name}",
        )
        task_run_summaries = sorted(
            (self._task_run_summary(task_run) for task_run in task_runs),
            key=lambda summary: summary["name"],
        )
        failed_task_run = next(
            (
                task_run
                for task_run in task_runs
                if self._task_run_summary(task_run)["status"] == "Failed"
            ),
            None,
        )

        return EvidenceItem(
            item_id=item_id,
            kind="pipeline_run",
            data={
                "pipeline_run_name": name,
                "pipeline_run_id": str(metadata.get("uid", "")),
                "namespace": namespace,
                "cluster_name": _param_value(spec, "cluster-name"),
                "pipeline_name": spec.get("pipelineRef", {}).get("name", ""),
                "params": _redacted_params(
                    spec, namespace, name, redactions
                ),
                "workspaces": [
                    _workspace_binding(workspace)
                    for workspace in spec.get("workspaces", [])
                ],
                "run_template": _run_template(spec),
                "status": self._status(resource),
                "start_time": status.get("startTime", ""),
                "completion_time": status.get("completionTime", ""),
                "conditions": self._condition_summaries(
                    status.get("conditions", [])
                ),
                "task_runs": task_run_summaries,
                "failed_task": (
                    self._failed_task(failed_task_run)
                    if failed_task_run is not None
                    else None
                ),
            },
        )

    @staticmethod
    def _task_runs_by_pipeline(
        task_runs: list[dict[str, Any]],
    ) -> dict[tuple[str, str], list[dict[str, Any]]]:
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for task_run in task_runs:
            metadata = task_run.get("metadata", {})
            labels = metadata.get("labels", {})
            key = (
                metadata.get("namespace", ""),
                labels.get("tekton.dev/pipelineRun", ""),
            )
            grouped.setdefault(key, []).append(task_run)
        return grouped

    @staticmethod
    def _condition_summaries(
        conditions: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        return [
            {
                "type": condition.get("type", ""),
                "status": condition.get("status", ""),
                "reason": condition.get("reason", ""),
            }
            for condition in conditions
        ]

    @staticmethod
    def _task_run_summary(task_run: dict[str, Any]) -> dict[str, Any]:
        metadata = task_run.get("metadata", {})
        labels = metadata.get("labels", {})
        conditions = task_run.get("status", {}).get("conditions", [])
        condition = next(
            (entry for entry in conditions if entry.get("type") == "Succeeded"),
            {},
        )
        return {
            "name": metadata.get("name", ""),
            "pipeline_task": labels.get("tekton.dev/pipelineTask", ""),
            "status": (
                "Succeeded"
                if condition.get("status") == "True"
                else "Failed"
                if condition.get("status") == "False"
                else "Unknown"
            ),
        }

    @staticmethod
    def _failed_task(task_run: dict[str, Any]) -> dict[str, Any]:
        summary = TektonInventoryCollector._task_run_summary(task_run)
        conditions = task_run.get("status", {}).get("conditions", [])
        condition = next(
            (entry for entry in conditions if entry.get("type") == "Succeeded"),
            {},
        )
        return {
            **summary,
            "reason": condition.get("reason", ""),
        }

    @staticmethod
    def _status(resource: dict[str, Any]) -> str:
        conditions = resource.get("status", {}).get("conditions", [])
        condition = next(
            (entry for entry in conditions if entry.get("type") == "Succeeded"),
            {},
        )
        if condition.get("status") == "True":
            return "Succeeded"
        if condition.get("status") == "False":
            return "Failed"
        return "Unknown"


def _param_value(spec: dict[str, Any], name: str) -> Any:
    for param in spec.get("params", []):
        if param.get("name") == name:
            return param.get("value")
    return None


def _redacted_params(
    spec: dict[str, Any],
    namespace: str,
    name: str,
    redactions: list[str],
) -> dict[str, Any]:
    params = redact_parameters(
        {
            str(param.get("name", "")): param.get("value")
            for param in spec.get("params", [])
        }
    )
    redactions.extend(
        f"{pipeline_run_redaction_prefix(namespace, name)}.params.{field}"
        for field in params.redacted_fields
    )
    return params.value


def pipeline_run_redaction_prefix(namespace: str, name: str) -> str:
    """Return the position-independent redaction path for one PipelineRun."""

    return f"pipeline_run[{namespace}/{name}]"


_WORKSPACE_REFERENCES = {
    "secret": "secretName",
    "configMap": "name",
    "persistentVolumeClaim": "claimName",
    "volumeClaimTemplate": None,
    "emptyDir": None,
    "csi": None,
    "projected": None,
}


_RUN_SPEC_FIELDS = frozenset(
    {"pipelineRef", "params", "workspaces", "taskRunTemplate", "timeouts"}
)
_POD_TEMPLATE_FIELDS = (
    "securityContext",
    "imagePullSecrets",
    "nodeSelector",
    "tolerations",
)


def _run_template(spec: dict[str, Any]) -> dict[str, Any]:
    """Record the run template a rerun copies, naming other fields only.

    Unsupported fields are named, never valued, because fields such as a
    pod template's env can hold secret material.
    """

    template = spec.get("taskRunTemplate") or {}
    pod_template = template.get("podTemplate") or {}
    unsupported = [
        *(f"spec.{key}" for key in spec if key not in _RUN_SPEC_FIELDS),
        *(
            f"spec.taskRunTemplate.{key}"
            for key in template
            if key not in {"serviceAccountName", "podTemplate"}
        ),
        *(
            f"spec.taskRunTemplate.podTemplate.{key}"
            for key in pod_template
            if key not in _POD_TEMPLATE_FIELDS
        ),
    ]
    return {
        "service_account_name": template.get("serviceAccountName"),
        "pod_template": {
            key: pod_template[key]
            for key in _POD_TEMPLATE_FIELDS
            if key in pod_template
        },
        "unsupported_fields": sorted(unsupported),
    }


def _workspace_binding(workspace: dict[str, Any]) -> dict[str, Any]:
    """Summarize a workspace binding by source and referenced object name."""

    source = next(
        (name for name in _WORKSPACE_REFERENCES if name in workspace),
        "unknown",
    )
    reference_key = _WORKSPACE_REFERENCES.get(source)
    details = workspace.get(source) or {}
    carried = {reference_key} if reference_key else set()
    if source == "volumeClaimTemplate":
        carried = {"spec"}
    unsupported = [
        *(
            key
            for key in workspace
            if key not in {"name", "subPath", source}
        ),
        *(f"{source}.{key}" for key in details if key not in carried),
    ]
    return {
        "name": workspace.get("name", ""),
        "source": source,
        "reference": details.get(reference_key) if reference_key else None,
        "sub_path": workspace.get("subPath"),
        "claim_spec": (
            details.get("spec") if source == "volumeClaimTemplate" else None
        ),
        "unsupported_fields": sorted(unsupported),
    }
