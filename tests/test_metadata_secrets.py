"""A definition whose metadata.yaml has secret-named keys fails closed (I6).

The Git collector reports it as cluster_definition_invalid, naming only
the keys, and stores none of the file's text.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from aura.collectors.git_inventory import GitInventoryCollector
from aura.contracts import CommandState, EvidenceSnapshot
from failure_injection import run_cli
from invariants import assert_command_invariants, record_files
from test_cli_diagnose_state import FakeClusterStateCollector
from test_cluster_edit_proposal import fleet
from test_failure_matrix import (
    COMMAND_ID,
    KEY,
    RECORD_DIRS,
    cluster,
    cluster_edit,
    operator,
    records,
)


SECRET = "s3cr3tvalue"
KEPT = "platform: aws\nhtpasswd-provider-name: Red Hat\n"


@pytest.mark.parametrize(
    ("line", "names"),
    [
        (f"token: {SECRET}", "token"),
        (f"pullSecret: {SECRET}", "pullSecret"),
        (f"credentials:\n  password: {SECRET}", "credentials.password"),
    ],
)
def test_collector_reports_secret_named_metadata_keys_as_invalid(
    tmp_path: Path,
    line: str,
    names: str,
) -> None:
    root = fleet(tmp_path, KEPT + line + "\n")

    snapshot = GitInventoryCollector(root).collect(command_id=COMMAND_ID)

    [item] = [i for i in snapshot.items if i.data.get("path") == "provision/example"]
    assert item.kind == "cluster_definition_invalid"
    assert item.data["error"] == (
        f"metadata.yaml contains secret-named keys: [{names}]"
    )
    assert "metadata_text" not in item.data
    assert SECRET not in snapshot.model_dump_json()


def test_collector_keeps_htpasswd_provider_name_valid(tmp_path: Path) -> None:
    root = fleet(tmp_path, KEPT + "aws-credentials-mode: legacy\n")

    [item] = GitInventoryCollector(root).collect(command_id=COMMAND_ID).items

    assert item.kind == "cluster_definition"


def stored_secret(root: Path, stdout: str) -> list[str]:
    leaked = [
        str(path.relative_to(root))
        for path, content in record_files(
            *(root / name for name in RECORD_DIRS)
        ).items()
        if SECRET.encode() in content
    ]
    return leaked + (["stdout"] if SECRET in stdout else [])


def run(root: Path, monkeypatch, arguments: list[str], collector: object):
    return run_cli(
        arguments,
        monkeypatch,
        collector=collector,
        command_id=COMMAND_ID,
        clock=lambda: datetime(2026, 1, 1, tzinfo=UTC),
    )


def check(root: Path) -> CommandState:
    record = assert_command_invariants(
        command_dir=root / "commands",
        idempotency_key=KEY,
        audit_dir=root / "audit",
        cwa_dir=root / "cwa",
        proposal_dir=root / "proposal",
    )
    return record.status


def test_inventory_lists_the_definition_as_invalid(tmp_path, monkeypatch) -> None:
    checkout = fleet(tmp_path, KEPT + f"token: {SECRET}\n")
    arguments = [
        "inventory",
        "list",
        "--fleet-clusters",
        str(checkout),
        *records(tmp_path, "snapshot", "audit", "cwa"),
        *operator(tmp_path),
    ]

    result = run(tmp_path, monkeypatch, arguments, None)

    assert result.exit_code == 0, result.stderr
    snapshot = EvidenceSnapshot.model_validate_json(result.stdout)
    assert [item.kind for item in snapshot.items] == [
        "cluster_definition_invalid"
    ]
    assert check(tmp_path) is CommandState.SUCCEEDED
    assert stored_secret(tmp_path, result.stdout) == []


def test_diagnosis_refuses_the_definition(tmp_path, monkeypatch) -> None:
    checkout = fleet(tmp_path, KEPT + f"token: {SECRET}\n")
    arguments = [
        "diagnose",
        "state",
        *cluster(tmp_path),
        *records(tmp_path, "snapshot", "audit", "cwa", "diagnosis"),
        *operator(tmp_path),
    ]
    collector = FakeClusterStateCollector(
        git_collector=GitInventoryCollector(checkout)
    )

    result = run(tmp_path, monkeypatch, arguments, collector)

    assert result.exit_code == 2
    assert "Valid Git cluster definition" in result.stderr
    assert check(tmp_path) is CommandState.REFUSED
    assert stored_secret(tmp_path, result.stdout) == []


def test_cluster_edit_refuses_the_definition(tmp_path, monkeypatch) -> None:
    arguments, collector = cluster_edit(KEPT + f"token: {SECRET}\n")(tmp_path)

    result = run(tmp_path, monkeypatch, arguments, collector)

    assert result.exit_code == 2
    assert result.stderr == (
        "aura: proposal refused: definition for example is invalid: "
        "metadata.yaml contains secret-named keys: [token]\n"
    )
    assert check(tmp_path) is CommandState.REFUSED
    assert stored_secret(tmp_path, result.stdout) == []
