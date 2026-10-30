"""The Command Guard, G1-G12 (Phase 3 task 3.3).

A valid context is built from a real submit and two real signed
approvals; each test changes one input and expects its rule's refusal.
"""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from aura.adapters import AdapterName
from aura.collectors.hub_policy import HubPolicy
from aura.config import SQLiteStore
from aura.contracts import CommandEnvelope, CommandState, CommandType
from aura.execution.commands import Refusal
from aura.execution.guard import GuardContext, GuardRefusal, check, g9_current_contract
from aura.execution.recording import RecordingAdapter
from aura.execution.store import ExecutionStore
from aura.proposals import ProposalType
from aura.signing import APPROVE, EXECUTE, sign
from test_execution_store import signed_execution
from test_proposal_approve import decide
from test_proposal_submit import phase2, proposal_id, submit, use_store  # noqa: F401
from test_signing import key, listed


NOW = datetime(2026, 10, 30, 13, tzinfo=UTC)


@pytest.fixture
def world(tmp_path, monkeypatch, phase2):
    """A published deprovision proposal with two approvals."""

    database = use_store(tmp_path, monkeypatch, login="proposer-a")
    assert submit(phase2)[0] == 0
    keys = {name: key(tmp_path, name) for name in ("first", "second", "executor")}
    signers = (
        listed("approver@example.com", keys["first"], APPROVE)
        + listed("second@example.com", keys["second"], APPROVE)
        + listed("executor@example.com", keys["executor"], EXECUTE)
    )
    hub = HubPolicy(allowed_signers=signers, deployment_id=UUID(int=1), pipeline_run_retention=None)
    for number, (principal, private) in enumerate(
        (("approver@example.com", keys["first"]), ("second@example.com", keys["second"])), 1
    ):
        config = tmp_path / f"{principal}.toml"
        config.write_text(
            f'[store]\nbackend = "sqlite"\npath = "{database}"\nlogin = "{principal}"\n\n'
            f'[signing]\nprincipal = "{principal}"\nkey = "{private}"\nagent = false\n',
            encoding="utf-8",
        )
        monkeypatch.setenv("AURA_CONFIG", str(config))
        code, _, stderr = decide(phase2, hub=hub, key_name=f"approve-{number}", command_id=UUID(int=700 + number))
        assert code == 0, stderr
    return {"database": database, "root": phase2, "keys": keys, "hub": hub, "tmp": tmp_path}


def context(world, **changes) -> GuardContext:
    store = ExecutionStore.open_sqlite(
        SQLiteStore(backend="sqlite", path=world["database"], login="executor@example.com")
    ).with_signers(world["hub"].allowed_signers)
    command = CommandEnvelope(
        command_id=UUID(int=800),
        command_type=CommandType.EXECUTE_PATCH,
        operator_id="executor@example.com",
        created_at=NOW,
        input={"proposal_id": proposal_id(world["root"])},
        idempotency_key="execute-1",
        status=CommandState.RECEIVED,
    )
    values = {
        "store": store,
        "policy": world["hub"],
        "command": command,
        "executor": "executor@example.com",
        "sign": lambda payload: sign(payload, key=world["keys"]["executor"], namespace=EXECUTE),
        "now": NOW,
        "backend": "sqlite",
        "deployment_id": store.deployment_id(),
        "enabled_adapters": frozenset({AdapterName.RECORDING}),
        "adapters": {AdapterName.RECORDING: RecordingAdapter()},
        "git_stale": lambda proposal: False,
        "regenerate": lambda proposal: proposal,
        "hub_effect": lambda proposal_id: False,
        "preflight": lambda adapter: None,
        **changes,
    }
    return GuardContext(**values)


def pid(world) -> UUID:
    return UUID(proposal_id(world["root"]))


def refused(rule: str, world, **changes) -> str:
    with pytest.raises(GuardRefusal) as refusal:
        check(context(world, **changes), pid(world))
    assert refusal.value.rule == rule, str(refusal.value)
    return str(refusal.value)


def test_a_valid_proposal_with_its_approvers_is_cleared(world) -> None:
    cleared = check(context(world), pid(world))

    assert cleared.published.proposal_id == pid(world)
    assert {a.principal for a in cleared.approvals} == {"approver@example.com", "second@example.com"}
    assert cleared.adapter.name is AdapterName.RECORDING


def test_g1_a_command_of_the_wrong_type_is_refused(world) -> None:
    command = context(world).command.model_copy(update={"command_type": CommandType.EXECUTE_PIPELINE})

    refused("G1", world, command=command)


def test_g2_an_executor_without_an_execute_key_is_refused(world) -> None:
    approver_key = world["keys"]["first"]

    refused("G2", world, sign=lambda payload: sign(payload, key=approver_key, namespace=EXECUTE))


def test_g3_an_unpublished_proposal_is_refused(world) -> None:
    with pytest.raises(GuardRefusal) as refusal:
        check(context(world), UUID(int=1))

    assert refusal.value.rule == "G3"


def test_g3_a_proposal_modified_after_approval_is_refused(world) -> None:
    connection = sqlite3.connect(world["database"], isolation_level=None)
    connection.execute("UPDATE published_proposals SET record = replace(record, 'deprovision of', 'deprovision for')")
    connection.close()

    refused("G3", world)


class Publishing:
    """Delegates to a store but returns a given publication, so a rule can
    see one that the contract would not let the store hold."""

    def __init__(self, store, published) -> None:
        self._store = store
        self._published = published

    def select(self, table, **equal):
        if table == "published_proposals":
            return [self._published]
        return self._store.select(table, **equal)

    def __getattr__(self, name):
        return getattr(self._store, name)


def altered(world, **changes) -> Publishing:
    store = context(world).store
    published = store.select("published_proposals")[0]
    return Publishing(store, published.model_copy(update=changes))


def test_g4_a_command_that_did_not_succeed_is_refused(world) -> None:
    published = context(world).store.select("published_proposals")[0]
    failed = published.command.model_copy(update={"status": CommandState.FAILED})

    refused("G4", world, store=altered(world, command=failed))


def test_g5_a_missing_approval_is_refused(world) -> None:
    connection = sqlite3.connect(world["database"], isolation_level=None)
    connection.execute("DELETE FROM approvals WHERE principal = 'second@example.com'")
    connection.close()

    refused("G5", world)


def test_g5_an_approval_by_a_key_without_the_approver_role_is_refused(world) -> None:
    keys = world["keys"]
    demoted = (
        listed("approver@example.com", keys["first"], EXECUTE)
        + listed("second@example.com", keys["second"], APPROVE)
        + listed("executor@example.com", keys["executor"], EXECUTE)
    )
    hub = replace(world["hub"], allowed_signers=demoted)

    refused("G5", world, policy=hub, store=context(world).store.with_signers(demoted))


def test_g5_the_executor_cannot_count_as_an_approver(world) -> None:
    keys = world["keys"]
    # second@example.com approved and may also execute.
    both = (
        listed("approver@example.com", keys["first"], APPROVE)
        + listed("second@example.com", keys["second"], f"{APPROVE},{EXECUTE}")
    )
    hub = replace(world["hub"], allowed_signers=both)

    refused(
        "G5", world, executor="second@example.com", policy=hub,
        store=context(world).store.with_signers(both),
        sign=lambda payload: sign(payload, key=keys["second"], namespace=EXECUTE),
    )


def test_g5_expired_approvals_are_refused(world) -> None:
    refused("G5", world, now=datetime(2026, 11, 5, tzinfo=UTC))


def test_g6_a_used_idempotency_key_is_refused(world) -> None:
    command = context(world).command.model_copy(update={"idempotency_key": "approve-1"})
    # The key of a stored command of the same login.
    store = ExecutionStore.open_sqlite(
        SQLiteStore(backend="sqlite", path=world["database"], login="approver@example.com")
    ).with_signers(world["hub"].allowed_signers)

    refused("G6", world, command=command, store=store, executor="executor@example.com")


def test_g6_an_earlier_effect_on_the_hub_is_refused(world) -> None:
    refused("G6", world, hub_effect=lambda proposal_id: True)


def test_g6_a_dispatched_proposal_is_not_dispatched_again(world) -> None:
    store_dispatch(world, proposal=pid(world))

    message = refused("G6", world)

    assert "awaiting_operator" in message


def store_dispatch(world, *, proposal: UUID, cluster: str = "example", record: int = 51) -> None:
    published = context(world).store.select("published_proposals")[0]
    execution, event = signed_execution(
        world["keys"]["executor"],
        record_id=str(UUID(int=record)),
        execution_id=str(UUID(int=record + 1000)),
        proposal_id=str(proposal),
        proposal_type="deprovision",
        adapter="recording",
        cluster_name=cluster,
        proposal_digest=published.proposal_digest,
    )
    store = context(world).store
    store.insert("audit_events", event)
    store.insert("executions", execution)


def test_g7_a_stale_proposal_is_refused(world) -> None:
    refused("G7", world, git_stale=lambda proposal: True)


def test_g8_a_regenerated_action_that_differs_is_refused(world) -> None:
    refused("G8", world, regenerate=lambda p: p.model_copy(update={"patch": "diff --git a/x b/x\n"}))


def test_g8_a_refused_regeneration_is_a_guard_refusal(world) -> None:
    def refuse(proposal):
        raise Refusal("the cluster is no longer in fleet-clusters")

    assert "no longer in fleet-clusters" in refused("G8", world, regenerate=refuse)


@pytest.mark.parametrize(
    "changes",
    [
        {"patch": "diff --git a/deprovision/example-archive b/deprovision/example-archive\n"
                  "new file mode 100644\n--- /dev/null\n+++ b/deprovision/example-archive\n"
                  "diff --git a/deprovision/other-archive b/deprovision/other-archive\n"
                  "new file mode 100644\n--- /dev/null\n+++ b/deprovision/other-archive\n"},
        {"patch": "diff --git a/README.md b/README.md\nnew file mode 100644\n--- /dev/null\n+++ b/README.md\n"},
        {"commit_message": None},
        {"patch": "diff --git a/deprovision/example b/deprovision/example\nnew file mode 100644\n"
                  "--- /dev/null\n+++ b/deprovision/example\n"},
        {"proposal_type": ProposalType.POST_PROVISION_RERUN, "patch": None, "commit_message": None,
         "pipeline_run": {"apiVersion": "tekton.dev/v1", "kind": "PipelineRun",
                          "metadata": {"generateName": "provision-", "namespace": "openshift-pipelines"},
                          "spec": {"pipelineRef": {"name": "provision"}}}},
    ],
    ids=["two-clusters", "out-of-scope-target", "missing-commit-message",
         "bare-deprovision-sentinel", "direct-provision-pipeline-run"],
)
def test_g9_a_proposal_outside_the_current_contract_is_refused(world, changes) -> None:
    """Each guard test case is refused. Its change alters the approved
    action, so G5 refuses it first; G9 alone refuses it as well."""

    proposal = context(world).store.select("published_proposals")[0].proposal
    broken = proposal.model_copy(update=changes)

    with pytest.raises(GuardRefusal):
        check(context(world, store=altered(world, proposal=broken)), pid(world))
    with pytest.raises(GuardRefusal, match="G9"):
        g9_current_contract(broken)


def test_g9_refuses_a_contract_break_outside_the_action(world) -> None:
    from aura.proposals import Reversibility

    proposal = context(world).store.select("published_proposals")[0].proposal
    # Reversibility is not in the action subset, so G5 and G8 still pass.
    broken = proposal.model_copy(update={"reversibility": Reversibility.IRREVERSIBLE})

    refused("G9", world, store=altered(world, proposal=broken))


def test_g10_an_adapter_that_is_not_enabled_is_refused(world) -> None:
    refused("G10", world, enabled_adapters=frozenset())


def test_g10_no_adapter_can_target_the_eventlistener() -> None:
    assert not [name for name in AdapterName if "listener" in name.value]


def test_g10_a_real_adapter_needs_the_store_the_hub_names(world) -> None:
    adapters = {AdapterName.GIT_HANDOFF: RecordingAdapter()}

    assert "requires the store the hub names" in refused(
        "G10", world, enabled_adapters=frozenset({AdapterName.GIT_HANDOFF}), adapters=adapters
    )
    refused("G10", world, enabled_adapters=frozenset({AdapterName.GIT_HANDOFF}),
            adapters=adapters, backend="postgresql")


def test_g11_another_open_execution_for_the_cluster_is_refused(world) -> None:
    store_dispatch(world, proposal=UUID(int=4242))

    refused("G11", world)


def test_g11_the_hourly_limit_is_refused(world) -> None:
    refused("G11", world, hourly_limit=0)


def test_g12_a_failed_preflight_is_refused(world) -> None:
    assert "cannot create PipelineRuns" in refused(
        "G12", world, preflight=lambda adapter: "cannot create PipelineRuns"
    )


def test_g2_runs_before_the_publication_is_read(world) -> None:
    """An unsigned caller learns nothing about whether a proposal exists."""

    approver_key = world["keys"]["first"]
    with pytest.raises(GuardRefusal) as refusal:
        check(
            context(world, sign=lambda payload: sign(payload, key=approver_key, namespace=EXECUTE)),
            UUID(int=1),
        )

    assert refusal.value.rule == "G2"


def test_g5_an_approval_stamped_after_the_guard_clock_is_refused(world) -> None:
    # The approvals were stamped 12:00; a guard clock before 11:55 sees them
    # as more than five minutes in the future.
    refused("G5", world, now=datetime(2026, 10, 30, 11, 54, tzinfo=UTC))


def test_a_store_read_failure_fails_and_is_not_a_refusal(world) -> None:
    from aura.execution.store import StoreReadFailed

    class Unreadable(Publishing):
        def select(self, table, **equal):
            if table == "executions":
                raise StoreReadFailed("executions read failed: no such table")
            return super().select(table, **equal)

    store = context(world).store
    published = store.select("published_proposals")[0]

    with pytest.raises(StoreReadFailed):
        check(context(world, store=Unreadable(store, published)), pid(world))


def test_g10_recording_is_refused_on_postgresql(world) -> None:
    assert "development" in refused("G10", world, backend="postgresql")


def test_g10_an_adapter_that_targets_the_eventlistener_is_refused(world) -> None:
    refused("G10", world, adapters={AdapterName.RECORDING: RecordingAdapter(target="eventlistener")})
