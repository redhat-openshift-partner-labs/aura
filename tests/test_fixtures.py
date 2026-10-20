from __future__ import annotations

import json
from pathlib import Path

from aura.contracts import EvidenceSnapshot
from aura.redaction import redact


FIXTURES = Path(__file__).parent / "fixtures"


def test_fleet_clusters_inventory_fixture_is_valid() -> None:
    fixture_path = FIXTURES / "fleet-clusters-inventory.json"
    snapshot = EvidenceSnapshot.model_validate(
        json.loads(fixture_path.read_text(encoding="utf-8"))
    )

    assert snapshot.collector == "git_inventory"
    assert snapshot.source == "fleet-clusters"
    assert snapshot.source_version == (
        "492bc8e07afddcd557441ddb7d72158de60ab8cb"
    )
    assert len(snapshot.items) == 1

    item = snapshot.items[0]
    assert item.kind == "cluster_definition"
    assert item.data["status"] == "active"
    assert item.data["cluster_name"] == "0171d46a-scopedaws"
    assert item.data["cloud_provider"] == "aws"
    assert item.data["region"] == "us-east-2"
    assert item.data["tier"] == "base"
    assert item.data["environment"] == "development"
    assert item.data["aws_credentials_mode"] == "scoped"
    assert item.data["crossplane_template"] == "crossplane-scoped"


def test_fleet_clusters_inventory_fixture_contains_no_secrets() -> None:
    fixture_path = FIXTURES / "fleet-clusters-inventory.json"
    value = json.loads(fixture_path.read_text(encoding="utf-8"))

    result = redact(value)

    assert result.redacted_fields == []

