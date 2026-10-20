from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol, final
from uuid import UUID, uuid4, uuid5

from aura.contracts import EvidenceItem, EvidenceSnapshot


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
            redactions=[],
        )

    def _read_pipeline_run(
        self,
        resource: dict[str, Any],
        *,
        command_id: UUID,
        source_version: str,
        task_runs: list[dict[str, Any]],
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
                "cluster_name": self._param_value(
                    spec,
                    "cluster-name",
                ),
                "pipeline_name": spec.get("pipelineRef", {}).get("name", ""),
                "status": self._status(resource),
                "start_time": status.get("startTime", ""),
                "completion_time": status.get("completionTime", ""),
                "conditions": status.get("conditions", []),
                "task_runs": task_run_summaries,
                "failed_task": (
                    self._failed_task(failed_task_run)
                    if failed_task_run is not None
                    else None
                ),
            },
        )

    @staticmethod
    def _param_value(spec: dict[str, Any], name: str) -> Any:
        for param in spec.get("params", []):
            if param.get("name") == name:
                return param.get("value")
        return None

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
            "message": condition.get("message", ""),
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
