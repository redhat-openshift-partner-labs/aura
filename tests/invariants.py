"""Project invariants I1-I4 from docs/development.md, checked on records."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from aura.command_store import CommandNotFound, JsonCommandStore
from aura.contracts import (
    AuditEvent,
    AuditEventType,
    CommandEnvelope,
    CommandState,
)
from aura.cwa import CWATrace
from aura.proposals import Proposal


TERMINAL_STATES = frozenset(
    {CommandState.SUCCEEDED, CommandState.FAILED, CommandState.REFUSED}
)
TERMINAL_EVENTS = {
    CommandState.FAILED: AuditEventType.COMMAND_FAILED,
    CommandState.REFUSED: AuditEventType.COMMAND_REFUSED,
}


def _record_paths(directory: Path | None) -> list[Path]:
    if directory is None or not directory.is_dir():
        return []
    return sorted(directory.glob("*.json"))


def record_files(*directories: Path) -> dict[Path, bytes]:
    """Return the content of every record under the given directories."""

    return {
        path: path.read_bytes()
        for directory in directories
        if directory.is_dir()
        for path in sorted(directory.rglob("*.json"))
    }


def audit_events(audit_dir: Path | None) -> list[AuditEvent]:
    return [
        AuditEvent.model_validate_json(path.read_text(encoding="utf-8"))
        for path in _record_paths(audit_dir)
    ]


def command_chain(
    audit_dir: Path | None,
    command_id: UUID,
) -> list[AuditEvent]:
    """Return one command's audit events in chain order, asserting I2."""

    return _linear_chain(
        [
            event
            for event in audit_events(audit_dir)
            if event.command_id == command_id
        ]
    )


def proposals_of(
    proposal_dir: Path | None,
    command_id: UUID,
) -> list[Proposal]:
    return [
        proposal
        for proposal in (
            Proposal.model_validate_json(path.read_text(encoding="utf-8"))
            for path in _record_paths(proposal_dir)
        )
        if proposal.command_id == command_id
    ]


def assert_command_invariants(
    *,
    command_dir: Path,
    idempotency_key: str,
    audit_dir: Path | None = None,
    cwa_dir: Path | None = None,
    proposal_dir: Path | None = None,
    expect_record: bool = True,
    before: dict[Path, bytes] | None = None,
) -> CommandEnvelope | None:
    """Assert I1-I4 and return the terminal command record, if any.

    before holds records that existed before the command ran; they must be
    unchanged, and only records written after them are held to I3.
    """

    before = before or {}
    directories = [
        directory
        for directory in (command_dir, audit_dir, cwa_dir, proposal_dir)
        if directory is not None and directory.is_dir()
    ]
    for directory in directories:
        partial = sorted(directory.rglob(".*.tmp"))
        assert not partial, f"partial record left behind: {partial}"
        empty = [p for p in directory.rglob("*.json") if not p.stat().st_size]
        assert not empty, f"empty record left behind: {empty}"
    for path, content in before.items():
        assert path.exists() and path.read_bytes() == content, (
            f"record changed or removed: {path}"
        )

    record = _terminal_record(command_dir, idempotency_key)
    if expect_record:
        assert record is not None, (
            f"no terminal command record for {idempotency_key}"
        )
        assert record.status in TERMINAL_STATES, record.status
    if record is not None:
        _assert_single_record(command_dir, record)

    events = audit_events(audit_dir)
    chains = {
        command_id: _linear_chain(
            [event for event in events if event.command_id == command_id]
        )
        for command_id in {event.command_id for event in events}
    }
    if record is not None and audit_dir is not None and audit_dir.is_dir():
        assert chains.get(record.command_id), (
            f"command {record.command_id} has no audit events"
        )
        _assert_chain_matches_record(chains[record.command_id], record)

    for path in _record_paths(proposal_dir):
        if path in before:
            continue
        proposal = Proposal.model_validate_json(path.read_text("utf-8"))
        if record is not None and proposal.command_id == record.command_id:
            assert record.status is CommandState.SUCCEEDED, (
                f"proposal left by a failed command: {proposal.proposal_id}"
            )
        _assert_proposal_provenance(
            proposal,
            cwa_dir,
            chains.get(proposal.command_id, []),
        )
    return record


def _terminal_record(
    command_dir: Path,
    idempotency_key: str,
) -> CommandEnvelope | None:
    try:
        return JsonCommandStore(command_dir).load(idempotency_key)
    except CommandNotFound:
        return None


def _assert_single_record(
    command_dir: Path,
    record: CommandEnvelope,
) -> None:
    matching = [
        path
        for path in _record_paths(command_dir)
        if CommandEnvelope.model_validate_json(path.read_text("utf-8"))
        .command_id
        == record.command_id
    ]
    assert len(matching) == 1, (
        f"command {record.command_id} must have exactly one terminal "
        f"record, found {len(matching)}"
    )


def _linear_chain(events: list[AuditEvent]) -> list[AuditEvent]:
    if not events:
        return []
    event_ids = {event.event_id for event in events}
    roots = [event for event in events if event.previous_event_id is None]
    assert len(roots) == 1, (
        f"audit chain must have exactly one root, found {len(roots)}"
    )
    successors: dict[UUID, AuditEvent] = {}
    for event in events:
        previous = event.previous_event_id
        if previous is None:
            continue
        assert previous in event_ids, (
            f"{event.event_type.value} names an unknown previous event"
        )
        assert previous not in successors, (
            f"{successors[previous].event_type.value} and "
            f"{event.event_type.value} share predecessor {previous}"
        )
        successors[previous] = event
    chain = [roots[0]]
    while chain[-1].event_id in successors:
        chain.append(successors[chain[-1].event_id])
    assert len(chain) == len(events), "audit chain is not connected"
    return chain


def _assert_chain_matches_record(
    chain: list[AuditEvent],
    record: CommandEnvelope,
) -> None:
    expected = TERMINAL_EVENTS.get(record.status)
    if expected is not None:
        assert chain[-1].event_type is expected, (
            f"last audit event must be {expected.value} for a "
            f"{record.status.value} command, found "
            f"{chain[-1].event_type.value}"
        )
        return
    terminal = {event.event_type for event in chain} & set(
        TERMINAL_EVENTS.values()
    )
    assert not terminal, f"succeeded command has {terminal}"


def _assert_proposal_provenance(
    proposal: Proposal,
    cwa_dir: Path | None,
    chain: list[AuditEvent],
) -> None:
    label = f"proposal {proposal.proposal_id}"
    trace_path = (
        cwa_dir / "traces" / f"{proposal.cwa_trace_id}.json"
        if cwa_dir is not None
        else None
    )
    assert trace_path is not None and trace_path.exists(), (
        f"{label} has no stored CWA trace"
    )
    trace = CWATrace.model_validate_json(trace_path.read_text("utf-8"))
    assert trace.response_digest == proposal.proposal_digest, (
        f"{label} trace digest does not match"
    )
    assert any(
        event.event_type is AuditEventType.PROPOSAL_CREATED
        and event.data.get("proposal_id") == str(proposal.proposal_id)
        and event.data.get("proposal_digest") == proposal.proposal_digest
        for event in chain
    ), f"{label} has no proposal_created event"
