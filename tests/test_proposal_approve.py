"""`proposal approve` and `proposal reject` (Phase 3 task 2.4)."""

from __future__ import annotations

import io
from collections.abc import Callable
from contextlib import redirect_stderr, redirect_stdout
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from aura.approvals import ApprovalDecision
from aura.cli import main
from aura.collectors.hub_policy import HubPolicy, HubPolicyError
from aura.contracts import AuditEventType, CommandState
from aura.execution.action import action_digest
from aura.signing import APPROVE, verify
from store_invariants import assert_store_command_invariants, chain, store_write_faults
from test_proposal_submit import open_store, phase2, proposal_id, submit, use_store  # noqa: F401
from test_signing import key, listed


DECIDE_ID = UUID(int=600)
NOW = datetime(2026, 10, 30, 12, tzinfo=UTC)
APPROVED = [
    "audit_events:command_received",
    "audit_events:approval_recorded",
    "approvals",
    "commands",
]


@pytest.fixture
def published(tmp_path, monkeypatch, phase2) -> tuple[Path, Path]:
    database = use_store(tmp_path, monkeypatch, login="proposer-a")
    assert submit(phase2)[0] == 0
    return database, phase2


def approver_config(tmp_path: Path, monkeypatch, database: Path, private: Path) -> None:
    config = tmp_path / "approver.toml"
    config.write_text(
        f'[store]\nbackend = "sqlite"\npath = "{database}"\nlogin = "approver-b"\n\n'
        f'[signing]\nprincipal = "approver@example.com"\nkey = "{private}"\n'
        "agent = false\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AURA_CONFIG", str(config))


def policy(*lines: str) -> HubPolicy:
    return HubPolicy(
        allowed_signers="".join(lines),
        deployment_id=uuid4(),
        pipeline_run_retention=None,
    )


def decide(
    root: Path,
    decision: str = "approve",
    *,
    hub: HubPolicy | Callable[[], HubPolicy],
    key_name: str = "decide-1",
    pid: str | None = None,
    command_id: UUID = DECIDE_ID,
):
    readings: list[Path] = []

    def read(kubeconfig: Path) -> HubPolicy:
        readings.append(kubeconfig)
        return hub() if callable(hub) else hub

    stdout, stderr = io.StringIO(), io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(
            [
                "proposal", decision,
                "--proposal-id", pid or proposal_id(root),
                "--openshift-kubeconfig", "/kube/aura-readonly",
                "--idempotency-key", key_name,
            ],
            command_id_factory=lambda: command_id,
            clock=lambda: NOW,
            hub_policy=read,
        )
    assert set(readings) <= {Path("/kube/aura-readonly")}
    return code, stdout.getvalue(), stderr.getvalue()


@pytest.fixture
def approver(tmp_path, monkeypatch, published) -> tuple[Path, Path, Path]:
    database, root = published
    private = key(tmp_path, "approver")
    approver_config(tmp_path, monkeypatch, database, private)
    return database, root, private


def test_an_approval_is_signed_and_stored(approver, monkeypatch) -> None:
    database, root, private = approver
    allowed = listed("approver@example.com", private, APPROVE)

    with store_write_faults(monkeypatch) as writes:
        code, stdout, stderr = decide(root, hub=policy(allowed))

    assert code == 0, stderr
    assert writes.labels == APPROVED
    store = open_store(database, login="approver-b").with_signers(allowed)
    (record,) = store.select("approvals")
    (publication,) = store.select("published_proposals")
    assert record.decision is ApprovalDecision.APPROVED
    assert record.principal == "approver@example.com"
    assert record.action_digest == action_digest(publication.proposal)
    assert record.proposal_digest == publication.proposal_digest
    assert record.expires_at == NOW + timedelta(hours=24)
    assert verify(
        record.signed_payload(),
        record.signature,
        principal="approver@example.com",
        namespace=APPROVE,
        signers=allowed,
        at=NOW,
    ) == "approver@example.com"
    events = chain(store, DECIDE_ID)
    assert [e.event_type for e in events] == [
        AuditEventType.COMMAND_RECEIVED,
        AuditEventType.APPROVAL_RECORDED,
    ]
    assert record.audit_event_digest == events[-1].event_digest
    assert assert_store_command_invariants(
        store, database, key="decide-1", command_id=DECIDE_ID
    ) is CommandState.SUCCEEDED


def test_a_rejection_has_no_expiry(approver) -> None:
    database, root, private = approver
    allowed = listed("approver@example.com", private, APPROVE)

    code, _, stderr = decide(root, "reject", hub=policy(allowed))

    assert code == 0, stderr
    store = open_store(database, login="approver-b").with_signers(allowed)
    (record,) = store.select("approvals")
    assert record.decision is ApprovalDecision.REJECTED
    assert record.expires_at is None


def refused(database: Path, stderr: str, message: str) -> None:
    assert message in stderr
    store = open_store(database, login="approver-b")
    assert store.select("approvals") == []
    assert assert_store_command_invariants(
        store, database, key="decide-1", command_id=DECIDE_ID
    ) is CommandState.REFUSED


def test_an_unpublished_proposal_is_refused(approver) -> None:
    database, root, private = approver

    code, _, stderr = decide(
        root,
        hub=policy(listed("approver@example.com", private, APPROVE)),
        pid=str(UUID(int=1)),
    )

    assert code == 2
    refused(database, stderr, "is not published")


@pytest.mark.parametrize(
    "allowed",
    [
        lambda private, other: listed("approver@example.com", other, APPROVE),
        lambda private, other: listed("approver@example.com", private, "aura-execute"),
        lambda private, other: listed("someone@example.com", private, APPROVE),
    ],
    ids=["unlisted-key", "wrong-namespace", "another-principal"],
)
def test_a_signature_the_hub_list_refuses_is_not_stored(tmp_path, approver, allowed) -> None:
    database, root, private = approver
    other = key(tmp_path, "other")

    code, _, stderr = decide(root, hub=policy(allowed(private, other)))

    assert code == 2
    refused(database, stderr, "approval signature refused")


def test_a_hub_read_failure_fails_the_command(approver) -> None:
    database, root, _ = approver

    def unreadable() -> HubPolicy:
        raise HubPolicyError("ConfigMap aura-allowed-signers not found in aura")

    code, _, stderr = decide(root, hub=unreadable)

    assert code == 2
    assert "aura-allowed-signers not found" in stderr
    store = open_store(database, login="approver-b")
    assert store.select("approvals") == []
    assert assert_store_command_invariants(
        store, database, key="decide-1", command_id=DECIDE_ID
    ) is CommandState.FAILED


def test_a_config_without_signing_writes_nothing(tmp_path, monkeypatch, published) -> None:
    database, root = published
    config = tmp_path / "unsigned.toml"
    config.write_text(
        f'[store]\nbackend = "sqlite"\npath = "{database}"\nlogin = "approver-b"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("AURA_CONFIG", str(config))

    with store_write_faults(monkeypatch) as writes:
        code, _, stderr = decide(root, hub=policy())

    assert code == 2
    assert "no [signing] section" in stderr
    assert writes.labels == []


def test_a_reused_key_exits_before_any_write(approver, monkeypatch) -> None:
    _, root, private = approver
    hub = policy(listed("approver@example.com", private, APPROVE))
    assert decide(root, hub=hub)[0] == 0

    with store_write_faults(monkeypatch) as writes:
        code, _, stderr = decide(root, hub=hub)

    assert code == 2
    assert "idempotency key already used" in stderr
    assert writes.labels == []


@pytest.mark.parametrize("fail_at", range(len(APPROVED)), ids=APPROVED)
def test_each_store_write_failure_fails_closed(approver, monkeypatch, fail_at) -> None:
    database, root, private = approver

    with store_write_faults(monkeypatch, fail_at) as writes:
        code, stdout, stderr = decide(
            root, hub=policy(listed("approver@example.com", private, APPROVE))
        )

    assert (code, stdout) == (2, "")
    store = open_store(database, login="approver-b")
    if APPROVED[fail_at] in {"approvals", "commands"}:
        # The approval and the terminal record are stored together.
        assert writes.labels == APPROVED
        assert store.select("commands") == []
        assert store.select("approvals") == []
        return
    assert writes.labels[fail_at + 1 :] == ["audit_events:command_failed", "commands"]
    (record,) = store.select("commands")
    assert record.status is CommandState.FAILED
    assert store.select("approvals") == []
    events = chain(store, DECIDE_ID)
    assert events[-1].event_type is AuditEventType.COMMAND_FAILED


def race_loser(monkeypatch) -> None:
    """Skip the up-front key check, as a run that read the key before the
    winner stored it would."""

    import aura.execution.commands as commands

    monkeypatch.setattr(commands, "_duplicate", lambda store, command: None)


def test_a_race_loser_stores_no_approval_and_names_the_winner(approver, monkeypatch) -> None:
    database, root, private = approver
    hub = policy(listed("approver@example.com", private, APPROVE))
    assert decide(root, hub=hub)[0] == 0
    race_loser(monkeypatch)
    loser = UUID(int=601)

    code, _, stderr = decide(root, hub=hub, command_id=loser)

    assert code == 2
    assert f"completed concurrently by command {DECIDE_ID}" in stderr
    store = open_store(database, login="approver-b").with_signers(
        listed("approver@example.com", private, APPROVE)
    )
    assert [record.command_id for record in store.select("approvals")] == [DECIDE_ID]
    events = chain(store, loser)
    assert events[-1].event_type is AuditEventType.COMMAND_FAILED
    assert str(DECIDE_ID) in events[-1].data["reason"]


def test_a_failed_race_loser_keeps_its_own_failure_last(approver, monkeypatch) -> None:
    database, root, private = approver
    assert decide(root, hub=policy(listed("approver@example.com", private, APPROVE)))[0] == 0
    race_loser(monkeypatch)
    loser = UUID(int=602)

    def unreachable() -> HubPolicy:
        raise HubPolicyError("hub unreachable")

    code, _, stderr = decide(root, hub=unreachable, command_id=loser)

    assert code == 2
    assert f"completed concurrently by command {DECIDE_ID}" in stderr
    events = chain(open_store(database, login="approver-b"), loser)
    assert [event.event_type for event in events] == [
        AuditEventType.COMMAND_RECEIVED,
        AuditEventType.COMMAND_FAILED,
    ]
    assert events[-1].data["reason"] == "hub unreachable"
