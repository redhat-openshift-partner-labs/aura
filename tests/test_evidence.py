from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from aura.contracts import EvidenceItem, EvidenceSnapshot


def make_snapshot(**overrides: object) -> EvidenceSnapshot:
    values: dict[str, object] = {
        "snapshot_id": "00000000-0000-0000-0000-000000000010",
        "command_id": "00000000-0000-0000-0000-000000000001",
        "collector": "git_inventory",
        "collector_version": "1.0.0",
        "collected_at": datetime(2026, 1, 1, tzinfo=UTC),
        "source": "fleet-clusters",
        "source_version": "0123456789abcdef0123456789abcdef01234567",
        "items": [
            {
                "item_id": "00000000-0000-0000-0000-000000000011",
                "kind": "cluster_definition",
                "data": {"cluster_name": "example"},
            }
        ],
        "redactions": ["secret.token"],
    }
    values.update(overrides)
    return EvidenceSnapshot.model_validate(values)


def test_valid_evidence_snapshot() -> None:
    snapshot = make_snapshot()

    assert snapshot.snapshot_id == UUID("00000000-0000-0000-0000-000000000010")
    assert snapshot.command_id == UUID("00000000-0000-0000-0000-000000000001")
    assert snapshot.collector == "git_inventory"
    assert snapshot.items[0].kind == "cluster_definition"
    assert snapshot.redactions == ["secret.token"]


def test_evidence_snapshot_round_trips_through_json() -> None:
    snapshot = make_snapshot()

    restored = EvidenceSnapshot.model_validate_json(snapshot.model_dump_json())

    assert restored == snapshot


def test_evidence_snapshot_allows_empty_items_for_missing_evidence() -> None:
    snapshot = make_snapshot(items=[])

    assert snapshot.items == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("snapshot_id", "not-a-uuid"),
        ("command_id", "not-a-uuid"),
        ("collector", ""),
        ("collector", "   "),
        ("collector_version", ""),
        ("collector_version", "   "),
        ("collected_at", "2026-01-01T00:00:00"),
        ("source", ""),
        ("source", "   "),
        ("source_version", ""),
        ("source_version", "   "),
        ("redactions", [""]),
    ],
)
def test_evidence_snapshot_rejects_invalid_fields(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        make_snapshot(**{field: value})


def test_evidence_item_rejects_invalid_data() -> None:
    with pytest.raises(ValidationError):
        EvidenceItem.model_validate(
            {
                "item_id": "not-a-uuid",
                "kind": "cluster_definition",
                "data": {"cluster_name": "example"},
            }
        )

