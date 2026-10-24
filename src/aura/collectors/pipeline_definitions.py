"""Parameter definitions of Tekton Pipelines on the hub."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol, final
from uuid import UUID, uuid5

from aura.collectors.openshift_cli_pipeline_client import PIPELINE_NAMESPACE
from aura.contracts import EvidenceItem, EvidenceSnapshot


PARAM_TYPES = frozenset({"string", "array", "object"})


class PipelineDefinitionError(Exception):
    """Raised when requested Pipelines are absent or a Pipeline is unnamed."""


class PipelineClient(Protocol):
    def list_pipelines(self) -> list[dict[str, Any]]:
        ...

    def resource_version(self) -> str:
        ...


@final
class PipelineDefinitionCollector:
    """Records which parameters each Pipeline declares and requires.

    Default values are never stored: validation needs only whether a
    default exists, and a default can hold secret material under any name.
    """

    collector_version = "0.1.0"

    def __init__(
        self,
        *,
        client: PipelineClient,
        pipeline_names: set[str] | None = None,
    ) -> None:
        self._client = client
        self._pipeline_names = pipeline_names

    def collect(
        self,
        *,
        command_id: UUID,
        snapshot_id: UUID | None = None,
        collected_at: datetime | None = None,
        source_version: str | None = None,
    ) -> EvidenceSnapshot:
        pipelines = [
            (_pipeline_name(pipeline), pipeline)
            for pipeline in self._client.list_pipelines()
        ]
        if self._pipeline_names is not None:
            missing = sorted(
                self._pipeline_names - {name for name, _ in pipelines}
            )
            if missing:
                raise PipelineDefinitionError(
                    f"requested Pipelines not found: {missing}"
                )
        items = [
            _definition_item(command_id, name, pipeline)
            for name, pipeline in pipelines
            if self._pipeline_names is None or name in self._pipeline_names
        ]
        return EvidenceSnapshot(
            snapshot_id=snapshot_id
            or uuid5(command_id, "pipeline-definitions"),
            command_id=command_id,
            collector="pipeline_definitions",
            collector_version=self.collector_version,
            collected_at=collected_at or datetime.now(UTC),
            source="tekton",
            source_version=source_version or self._client.resource_version(),
            items=items,
            redactions=[],
        )


def _metadata(pipeline: dict[str, Any]) -> dict[str, Any]:
    metadata = pipeline.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def _pipeline_name(pipeline: dict[str, Any]) -> str:
    name = _metadata(pipeline).get("name")
    if not isinstance(name, str) or not name:
        namespace = _metadata(pipeline).get("namespace") or PIPELINE_NAMESPACE
        raise PipelineDefinitionError(
            f"Pipeline without a name in {namespace}"
        )
    return name


def _definition_item(
    command_id: UUID,
    name: str,
    pipeline: dict[str, Any],
) -> EvidenceItem:
    namespace = str(_metadata(pipeline).get("namespace") or PIPELINE_NAMESPACE)
    spec = pipeline.get("spec")
    entries = spec.get("params", []) if isinstance(spec, dict) else None
    params: list[dict[str, Any]] = []
    malformed = 0
    if not isinstance(entries, list):
        entries, malformed = [], 1
    for entry in entries:
        definition = _param(entry)
        if definition is None or definition["name"] in {
            known["name"] for known in params
        }:
            malformed += 1
        else:
            params.append(definition)
    return EvidenceItem(
        item_id=uuid5(command_id, f"pipeline:{namespace}/{name}"),
        kind="pipeline_definition",
        data={
            "namespace": namespace,
            "pipeline_name": name,
            "params": params,
            "required_params": [
                entry["name"] for entry in params if not entry["has_default"]
            ],
            "malformed_params": malformed,
        },
    )


def _param(entry: object) -> dict[str, Any] | None:
    """Return one parameter definition, or None when it is malformed."""

    if not isinstance(entry, dict):
        return None
    name = entry.get("name")
    param_type = entry.get("type", "string")
    valid_type = isinstance(param_type, str) and param_type in PARAM_TYPES
    if not isinstance(name, str) or not name or not valid_type:
        return None
    # Tekton treats a null default as no default, so the parameter is required.
    return {
        "name": name,
        "type": param_type,
        "has_default": entry.get("default") is not None,
    }


__all__ = [
    "PARAM_TYPES",
    "PipelineClient",
    "PipelineDefinitionCollector",
    "PipelineDefinitionError",
]
