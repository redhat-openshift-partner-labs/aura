"""PublishedProposal and ApprovalRecord contracts (Phase 3 task 2.2)."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from pydantic import ValidationError

from aura.approvals import (
    ApprovalDecision,
    ApprovalRecord,
    PublishedProposal,
    publication_id_for,
)
from aura.canonical import canonical_json
from aura.contracts import AuditEvent, CommandEnvelope, CommandType
from aura.cwa import CWATrace
from failure_injection import run_cli
from test_failure_matrix import COMMAND_ID, KEY, scenario_named


SIGNATURE = (
    "-----BEGIN SSH SIGNATURE-----\n"
    "U1NIU0lHAAAAAQAAADMAAAALc3NoLWVkMjU1MTk=\n"
    "-----END SSH SIGNATURE-----\n"
)
CREATED = datetime(2026, 10, 29, 12, tzinfo=UTC)


def proposal_records(tmp_path: Path, monkeypatch) -> dict[str, Any]:
    arguments, collector = scenario_named("propose").build(tmp_path)
    run = run_cli(
        arguments,
        monkeypatch,
        collector=collector,
        command_id=COMMAND_ID,
        clock=lambda: datetime(2026, 1, 1, tzinfo=UTC),
    )
    assert run.exit_code == 0, run.stderr

    def only(directory: Path) -> dict[str, Any]:
        (path,) = directory.glob("*.json")
        return json.loads(path.read_text(encoding="utf-8"))

    proposal = only(tmp_path / "proposal")
    events = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in (tmp_path / "audit").glob("*.json")
    ]
    (created,) = [e for e in events if e["event_type"] == "proposal_created"]
    command_path = tmp_path / "commands" / (
        hashlib.sha256(KEY.encode()).hexdigest() + ".json"
    )
    return {
        "publication_id": str(publication_id_for(UUID(proposal["proposal_id"]))),
        "proposal_id": proposal["proposal_id"],
        "proposal_digest": proposal["proposal_digest"],
        "proposal": proposal,
        "cwa_trace": only(tmp_path / "cwa" / "traces"),
        "proposal_created": created,
        "command": json.loads(command_path.read_text(encoding="utf-8")),
        "created_at": "2026-10-29T12:00:00Z",
    }


@pytest.fixture
def published(tmp_path, monkeypatch) -> dict[str, Any]:
    return proposal_records(tmp_path, monkeypatch)


def test_a_published_proposal_validates_and_digests(published) -> None:
    record = PublishedProposal.model_validate(published)

    assert len(record.publication_digest) == 64
    assert record.proposal.proposal_digest == record.proposal_digest


def changed(document: dict[str, Any], path: str, value: object) -> dict[str, Any]:
    """Return a deep copy with one nested value changed."""

    copy = json.loads(json.dumps(document))
    target = copy
    *parents, last = path.split(".")
    for name in parents:
        target = target[name]
    target[last] = value
    return copy


@pytest.mark.parametrize(
    ("path", "value"),
    [
        ("publication_id", str(UUID(int=5))),
        ("proposal_id", str(UUID(int=6))),
        ("proposal_digest", "1" * 64),
        ("cwa_trace.trace_digest", ""),
        ("proposal.proposal_digest", ""),
        ("proposal_created.event_digest", ""),
        ("command.record_digest", ""),
    ],
    ids=[
        "publication-id",
        "proposal-id",
        "proposal-digest",
        "trace-without-digest",
        "proposal-without-digest",
        "event-without-digest",
        "command-without-digest",
    ],
)
def test_a_published_proposal_rejects_an_inconsistent_bundle(
    published, path, value
) -> None:
    with pytest.raises(ValidationError):
        PublishedProposal.model_validate(changed(published, path, value))


NESTED = {
    "cwa_trace": (CWATrace, ("trace_digest",)),
    "proposal_created": (AuditEvent, ("event_digest",)),
    "command": (CommandEnvelope, ("request_digest", "record_digest")),
}


def consistent(published, name: str, path: str, value: object) -> dict[str, Any]:
    """Change one field of a nested record and recompute its digests, so
    only the bundle check can catch the change."""

    model, digests = NESTED[name]
    nested = changed(published[name], path, value)
    nested.update({digest: "" for digest in digests})
    record = model.model_validate(nested).model_dump(mode="json")
    return dict(published, **{name: record})


@pytest.mark.parametrize(
    ("name", "path", "value", "message"),
    [
        ("cwa_trace", "response_digest", "1" * 64, "response_digest"),
        ("cwa_trace", "trace_id", str(UUID(int=99)), "trace_id"),
        ("cwa_trace", "command_id", str(UUID(int=99)), "cwa_trace command_id"),
        ("proposal_created", "event_type", "command_received", "event_type"),
        ("proposal_created", "command_id", str(UUID(int=99)),
         "proposal_created command_id"),
        ("proposal_created", "data.proposal_id", str(UUID(int=99)),
         "proposal_created proposal_id"),
        ("proposal_created", "data.proposal_digest", "1" * 64,
         "proposal_created proposal_digest"),
        ("command", "command_id", str(UUID(int=99)), "command command_id"),
        ("command", "status", "failed", "command status"),
    ],
    ids=[
        "trace-response",
        "trace-id",
        "trace-command",
        "event-type",
        "event-command",
        "event-proposal-id",
        "event-proposal-digest",
        "command-id",
        "command-status",
    ],
)
def test_a_published_proposal_rejects_a_record_of_another_proposal(
    published, name, path, value, message
) -> None:
    with pytest.raises(ValidationError, match=message):
        PublishedProposal.model_validate(
            consistent(published, name, path, value)
        )


def approval(**changes: object) -> dict[str, Any]:
    return {
        "approval_id": str(UUID(int=10)),
        "command_id": str(UUID(int=11)),
        "proposal_id": str(UUID(int=12)),
        "proposal_digest": "2" * 64,
        "action_digest": "3" * 64,
        "decision": "approved",
        "principal": "approver@example.com",
        "signature": SIGNATURE,
        "created_at": CREATED.isoformat(),
        "expires_at": (CREATED + timedelta(hours=24)).isoformat(),
        "audit_event_digest": "4" * 64,
        **changes,
    }


def test_an_approval_validates_and_digests() -> None:
    record = ApprovalRecord.model_validate(approval())

    assert record.decision is ApprovalDecision.APPROVED
    assert len(record.approval_digest) == 64


def test_a_rejection_has_no_expiry() -> None:
    record = ApprovalRecord.model_validate(
        approval(decision="rejected", expires_at=None)
    )

    assert record.expires_at is None


@pytest.mark.parametrize(
    "changes",
    [
        {"expires_at": None},
        {"expires_at": (CREATED + timedelta(hours=24, seconds=1)).isoformat()},
        {"expires_at": CREATED.isoformat()},
        {"decision": "rejected"},
        {"decision": "maybe"},
        {"principal": " "},
        {"signature": "not a signature"},
        {"proposal_digest": "short"},
        {"action_digest": "short"},
        {"audit_event_digest": "short"},
        {"approval_digest": "5" * 64},
        {"created_at": "2026-10-29T12:00:00"},
        {"extra": 1},
    ],
    ids=[
        "approval-without-expiry",
        "expiry-beyond-24-hours",
        "expiry-not-after-creation",
        "rejection-with-expiry",
        "unknown-decision",
        "blank-principal",
        "bad-signature",
        "bad-proposal-digest",
        "bad-action-digest",
        "bad-audit-event-digest",
        "stale-approval-digest",
        "naive-time",
        "extra-field",
    ],
)
def test_an_approval_rejects_an_invalid_value(changes) -> None:
    with pytest.raises(ValidationError):
        ApprovalRecord.model_validate(approval(**changes))


def test_the_signed_payload_omits_only_the_signature_and_digest() -> None:
    record = ApprovalRecord.model_validate(approval())
    expected = record.model_dump(
        mode="json", exclude={"signature", "approval_digest"}
    )

    assert record.signed_payload() == canonical_json(expected).encode()


def test_publication_ids_are_deterministic() -> None:
    proposal_id = UUID(int=12)

    assert publication_id_for(proposal_id) == publication_id_for(proposal_id)
    assert publication_id_for(proposal_id) != publication_id_for(UUID(int=13))


def test_the_new_command_types_exist() -> None:
    assert {
        CommandType.PROPOSAL_SUBMIT,
        CommandType.PROPOSAL_APPROVE,
        CommandType.PROPOSAL_REJECT,
    } == {
        CommandType("proposal.submit"),
        CommandType("proposal.approve"),
        CommandType("proposal.reject"),
    }
