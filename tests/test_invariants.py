from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from aura.audit_store import JsonAuditEventStore
from aura.command_store import JsonCommandStore
from aura.cli import main
from aura.contracts import AuditEvent, AuditEventType, CommandEnvelope
from aura.proposal_store import JsonProposalStore
from invariants import assert_command_invariants, record_files
from test_cli_propose import (
    cluster_state_collector,
    fleet_clusters,
    propose_arguments,
)
from test_proposals import added_file, make_proposal


def proposed(tmp_path: Path) -> Path:
    collector = cluster_state_collector(fleet_clusters(tmp_path))
    exit_code = main(
        propose_arguments(tmp_path, "proposer"),
        collector=collector,
        command_id_factory=lambda: UUID(int=1),
        clock=lambda: datetime(2026, 1, 1, tzinfo=UTC),
    )
    assert exit_code == 0
    return tmp_path


def check(
    root: Path,
    *,
    expect_record: bool = True,
    before: dict[Path, bytes] | None = None,
) -> None:
    assert_command_invariants(
        command_dir=root / "commands",
        idempotency_key="propose-key",
        audit_dir=root / "audit",
        cwa_dir=root / "cwa",
        proposal_dir=root / "proposals",
        expect_record=expect_record,
        before=before,
    )


def stored_events(root: Path) -> dict[str, AuditEvent]:
    return {
        event.event_type.value: event
        for event in (
            AuditEvent.model_validate_json(path.read_text())
            for path in (root / "audit").glob("*.json")
        )
    }


def add_event(root: Path, event_type: AuditEventType, previous: UUID | None):
    JsonAuditEventStore(root / "audit").save(
        AuditEvent(
            event_id=UUID(int=900),
            command_id=UUID(int=1),
            event_type=event_type,
            occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
            actor="operator@example.com",
            data={},
            previous_event_id=previous,
        )
    )


def stored_records(root: Path) -> dict[Path, bytes]:
    return record_files(
        *(root / name for name in ("commands", "audit", "cwa", "proposals"))
    )


def test_invariants_accept_successful_proposal(tmp_path) -> None:
    check(proposed(tmp_path))


def _fork(root: Path) -> None:
    context = stored_events(root)["context_assembled"]
    add_event(root, AuditEventType.COMMAND_FAILED, context.event_id)


def _dangle(root: Path) -> None:
    add_event(root, AuditEventType.COMMAND_FAILED, UUID(int=901))


def _second_root(root: Path) -> None:
    add_event(root, AuditEventType.COMMAND_FAILED, None)


def _drop_trace(root: Path) -> None:
    for path in (root / "cwa" / "traces").glob("*.json"):
        path.unlink()


def _drop_proposal_event(root: Path) -> None:
    created = stored_events(root)["proposal_created"]
    (root / "audit" / f"{created.event_id}.json").unlink()


def _leave_temporary_file(root: Path) -> None:
    (root / "proposals" / ".record.json.0123.tmp").write_text("")


def _mark_failed(root: Path) -> None:
    path = next((root / "commands").glob("*.json"))
    path.chmod(0o600)
    record = json.loads(path.read_text())
    record["status"] = "failed"
    record["record_digest"] = ""
    path.write_text(
        CommandEnvelope.model_validate(record).model_dump_json(indent=2)
    )


def _empty_record(root: Path) -> None:
    (root / "audit" / "empty.json").write_text("")


def _foreign_proposal(root: Path) -> None:
    JsonProposalStore(root / "proposals").save(
        make_proposal(
            "deprovision",
            proposal_id=UUID(int=56),
            command_id=UUID(int=55),
            cluster_name="example",
            patch=added_file("deprovision/example-archive"),
            commit_message="chore: deprovision example",
        )
    )


def _fail_after_proposal(root: Path) -> None:
    _mark_failed(root)
    created = stored_events(root)["proposal_created"]
    add_event(root, AuditEventType.COMMAND_FAILED, created.event_id)


def _duplicate_record(root: Path) -> None:
    store = JsonCommandStore(root / "commands")
    record = store.load("propose-key")
    store.save(
        CommandEnvelope.model_validate(
            {
                **record.model_dump(mode="json"),
                "idempotency_key": "other-key",
                "request_digest": "",
                "record_digest": "",
            }
        )
    )


def _drop_events(root: Path) -> None:
    for path in (root / "audit").glob("*.json"):
        path.unlink()


@pytest.mark.parametrize(
    ("damage", "message"),
    [
        (_empty_record, "empty record"),
        (_foreign_proposal, "has no stored CWA trace"),
        (_fail_after_proposal, "proposal left by a failed command"),
        (_duplicate_record, "exactly one terminal record"),
        (_drop_events, "has no audit events"),
        (_fork, "share predecessor"),
        (_dangle, "unknown previous event"),
        (_second_root, "exactly one root"),
        (_drop_trace, "has no stored CWA trace"),
        (_drop_proposal_event, "has no proposal_created event"),
        (_leave_temporary_file, "partial record"),
        (_mark_failed, "last audit event must be command_failed"),
    ],
)
def test_invariants_reject_violations(tmp_path, damage, message) -> None:
    root = proposed(tmp_path)
    damage(root)

    with pytest.raises(AssertionError, match=message):
        check(root)


def test_invariants_require_terminal_record(tmp_path) -> None:
    root = proposed(tmp_path)
    for path in (root / "commands").glob("*.json"):
        path.unlink()

    with pytest.raises(AssertionError, match="no terminal command record"):
        check(root)
    check(root, expect_record=False)


def test_invariants_reject_changed_records(tmp_path) -> None:
    root = proposed(tmp_path)
    before = stored_records(root)
    trace = next((root / "cwa" / "traces").glob("*.json"))
    trace.chmod(0o600)
    trace.write_text(trace.read_text() + " ")

    with pytest.raises(AssertionError, match="record changed or removed"):
        check(root, before=before)


def test_invariants_skip_proposals_that_existed_before(tmp_path) -> None:
    root = proposed(tmp_path)
    _foreign_proposal(root)

    check(root, before=stored_records(root))
