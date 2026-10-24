"""Parameter definitions of Tekton Pipelines on the hub."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol, final
from uuid import UUID, uuid5

from aura.contracts import EvidenceItem, EvidenceSnapshot
from aura.redaction import redact_parameters


class PipelineClient(Protocol):
    def list_pipelines(self) -> list[dict[str, Any]]:
        ...

    def resource_version(self) -> str:
        ...


@final
class PipelineDefinitionCollector:
    """Records each Pipeline's parameters, with secret-named defaults
    redacted and malformed entries counted rather than dropped."""

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
        redactions: list[str] = []
        items = [
            _definition_item(command_id, pipeline, redactions)
            for pipeline in self._client.list_pipelines()
            if self._pipeline_names is None
            or _metadata(pipeline).get("name") in self._pipeline_names
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
            redactions=sorted(redactions),
        )


def pipeline_redaction_prefix(namespace: str, name: str) -> str:
    """Return the position-independent redaction path for one Pipeline."""

    return f"pipeline[{namespace}/{name}]"


def _metadata(pipeline: dict[str, Any]) -> dict[str, Any]:
    metadata = pipeline.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def _definition_item(
    command_id: UUID,
    pipeline: dict[str, Any],
    redactions: list[str],
) -> EvidenceItem:
    namespace = str(_metadata(pipeline).get("namespace", ""))
    name = str(_metadata(pipeline).get("name", ""))
    spec = pipeline.get("spec")
    entries = spec.get("params", []) if isinstance(spec, dict) else []
    malformed_list = not isinstance(entries, list)
    if malformed_list:
        entries = []
    declared = [
        entry
        for entry in entries
        if isinstance(entry, dict)
        and isinstance(entry.get("name"), str)
        and entry["name"]
    ]
    defaults = redact_parameters(
        {
            entry["name"]: entry["default"]
            for entry in declared
            if "default" in entry
        }
    )
    redactions.extend(
        f"{pipeline_redaction_prefix(namespace, name)}.params.{field}"
        for field in defaults.redacted_fields
    )
    params = []
    for entry in declared:
        definition = {
            "name": entry["name"],
            "type": str(entry.get("type") or "string"),
            "has_default": "default" in entry,
        }
        if "default" in entry:
            definition["default"] = defaults.value[entry["name"]]
        params.append(definition)
    return EvidenceItem(
        item_id=uuid5(command_id, f"pipeline:{namespace}/{name}"),
        kind="pipeline_definition",
        data={
            "namespace": namespace,
            "pipeline_name": name,
            "params": params,
            "required_params": [
                entry["name"] for entry in declared if "default" not in entry
            ],
            "malformed_params": len(entries)
            - len(declared)
            + int(malformed_list),
        },
    )


__all__ = [
    "PipelineClient",
    "PipelineDefinitionCollector",
    "pipeline_redaction_prefix",
]
