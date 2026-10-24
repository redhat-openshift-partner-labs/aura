"""Evidence for a post-provision rerun proposal."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol, final
from uuid import UUID, uuid5

from aura.contracts import EvidenceSnapshot


class SnapshotCollector(Protocol):
    def collect(
        self,
        *,
        command_id: UUID,
        snapshot_id: UUID | None = None,
        collected_at: datetime | None = None,
        source_version: str | None = None,
    ) -> EvidenceSnapshot:
        ...


@final
class RerunEvidenceCollector:
    """Combines one cluster's state with the post-provision Pipeline
    definition the rerun is checked against."""

    collector_version = "0.1.0"

    def __init__(
        self,
        *,
        cluster_state: SnapshotCollector,
        pipeline_definitions: SnapshotCollector,
    ) -> None:
        self._cluster_state = cluster_state
        self._pipeline_definitions = pipeline_definitions

    def collect(
        self,
        *,
        command_id: UUID,
        snapshot_id: UUID | None = None,
        collected_at: datetime | None = None,
        source_version: str | None = None,
    ) -> EvidenceSnapshot:
        collected_at = collected_at or datetime.now(UTC)
        state = self._cluster_state.collect(
            command_id=command_id,
            collected_at=collected_at,
        )
        definitions = self._pipeline_definitions.collect(
            command_id=command_id,
            collected_at=collected_at,
        )
        return EvidenceSnapshot(
            snapshot_id=snapshot_id
            or uuid5(command_id, "post-provision-rerun"),
            command_id=command_id,
            collector="post_provision_rerun",
            collector_version=self.collector_version,
            collected_at=collected_at,
            source=state.source,
            source_version=source_version
            or f"{state.source_version};{definitions.source_version}",
            items=[*state.items, *definitions.items],
            redactions=sorted({*state.redactions, *definitions.redactions}),
        )


__all__ = ["RerunEvidenceCollector"]
