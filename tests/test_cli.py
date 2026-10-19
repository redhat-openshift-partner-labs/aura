from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import final
from uuid import UUID

from aura.cli import main
from aura.contracts import EvidenceSnapshot


@final
class FakeCollector:
    def collect(
        self,
        *,
        command_id: UUID,
        snapshot_id: UUID | None = None,
        collected_at: datetime | None = None,
        source_version: str | None = None,
    ) -> EvidenceSnapshot:
        return EvidenceSnapshot(
            snapshot_id=UUID(int=50),
            command_id=command_id,
            collector="git_inventory",
            collector_version="test",
            collected_at=datetime(2026, 1, 1, tzinfo=UTC),
            source="fleet-clusters",
            source_version="test",
            items=[],
            redactions=[],
        )


def test_cli_inventory_list_writes_snapshot_json(
    tmp_path,
    capsys,
) -> None:
    exit_code = main(
        [
            "inventory",
            "list",
            "--fleet-clusters",
            str(tmp_path),
            "--operator",
            "operator@example.com",
            "--idempotency-key",
            "test-key",
        ],
        collector=FakeCollector(),
        command_id_factory=lambda: UUID(int=1),
    )

    assert exit_code == 0
    captured = capsys.readouterr()
    snapshot = json.loads(captured.out)
    assert snapshot["command_id"] == str(UUID(int=1))
    assert snapshot["collector"] == "git_inventory"
    assert captured.err == ""
