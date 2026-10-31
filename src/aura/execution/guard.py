"""The Phase 3 Command Guard: rules G1-G12, run in order before any adapter
call (task 3.3). The first rule that fails refuses the command, naming the
rule; only a command that passes every rule may dispatch.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from pydantic import ValidationError

from aura.adapters import AdapterName, ExecutionAdapter
from aura.approvals import ApprovalDecision, ApprovalRecord, PublishedProposal
from aura.canonical import canonical_json
from aura.collectors.hub_policy import HubPolicy
from aura.contracts import CommandEnvelope, CommandState, CommandType
from aura.execution.action import action_digest
from aura.execution.commands import Refusal
from aura.execution.derived import (
    OPEN_STATES,
    DerivedState,
    derive_state,
    execution_state,
    required_approvers,
)
from aura.execution.records import ExecutionRecord
from aura.execution.store import ExecutionStore, RecordInvalid
from aura.proposals import GIT_PROPOSAL_TYPES, Proposal
from aura.signing import CLOCK_MARGIN, EXECUTE, SignatureError, require_not_future, verify


DEFAULT_HOURLY_LIMIT = 5


class GuardRefusal(Refusal):
    """A guard rule refused the command."""

    def __init__(self, rule: str, reason: str) -> None:
        super().__init__(f"{rule}: {reason}")
        self.rule = rule


@dataclass(frozen=True)
class GuardContext:
    """Everything the rules read; tests supply each part."""

    store: ExecutionStore
    policy: HubPolicy
    command: CommandEnvelope
    executor: str
    sign: Callable[[bytes], str]
    now: datetime
    # The execution store's backend and deployment ID (G10).
    backend: str
    deployment_id: str
    enabled_adapters: frozenset[AdapterName]
    adapters: dict[AdapterName, ExecutionAdapter]
    git_stale: Callable[[Proposal], bool]
    # Re-collects evidence and regenerates the proposal, storing the
    # evidence, CWA records, and regenerated proposal (G8, task 3.4).
    regenerate: Callable[[Proposal], Proposal]
    # Whether the hub already holds a PipelineRun for the proposal (G6).
    hub_effect: Callable[[UUID], bool]
    # A read-only check that the adapter can reach its system (G12).
    preflight: Callable[[AdapterName], str | None]
    hourly_limit: int = DEFAULT_HOURLY_LIMIT
    # Recorded on the dispatching record for `execution status`.
    adapter_timeout_seconds: int = 300


@dataclass(frozen=True)
class Cleared:
    """What a command that passed the guard dispatches."""

    published: PublishedProposal
    approvals: list[ApprovalRecord]
    adapter: ExecutionAdapter
    action_digest: str


def check(context: GuardContext, proposal_id: UUID) -> Cleared:
    # G2 runs first, so an unsigned caller learns nothing about the
    # proposal; G1 reads the proposal's type, so the publication loads
    # (G3) before it.
    g2_signed_by_an_executor(context)
    published = g3_published_proposal(context, proposal_id)
    g1_command_type(context, published)
    g4_succeeded_command(published)
    action = action_digest(published.proposal)
    approvals = g5_approvals(context, published.proposal, action)
    g6_one_execution(context, published.proposal, approvals)
    g7_not_stale(context, published.proposal)
    g8_regenerates(context, published.proposal, action)
    g9_current_contract(published.proposal)
    adapter = g10_adapter(context, published.proposal)
    g11_cluster_and_rate(context, published.proposal)
    g12_preflight(context, adapter)
    return Cleared(published, approvals, adapter, action)


def _published_or_refuse(context: GuardContext, proposal_id: UUID) -> PublishedProposal:
    try:
        published = context.store.select("published_proposals", proposal_id=proposal_id)
    except RecordInvalid as error:
        # A record that fails verification is refused; a store that cannot
        # be read fails the command (failure-mode matrix).
        raise GuardRefusal("G3", f"published proposal is invalid: {error}") from error
    if not published:
        raise GuardRefusal("G3", f"proposal {proposal_id} is not published")
    return published[0]


def g1_command_type(context: GuardContext, published: PublishedProposal) -> None:
    expected = (
        CommandType.EXECUTE_PATCH
        if published.proposal.proposal_type in GIT_PROPOSAL_TYPES
        else CommandType.EXECUTE_PIPELINE
    )
    if context.command.command_type is not expected:
        raise GuardRefusal(
            "G1", f"{published.proposal.proposal_type.value} proposals run as {expected.value}"
        )


def g2_signed_by_an_executor(context: GuardContext) -> None:
    """Prove the executor holds a key listed for aura-execute (D7)."""

    payload = canonical_json(
        {"command_id": str(context.command.command_id), "purpose": "execute"}
    ).encode("utf-8")
    try:
        verify(
            payload,
            context.sign(payload),
            principal=context.executor,
            namespace=EXECUTE,
            signers=context.policy.allowed_signers,
            at=context.now,
        )
    except SignatureError as error:
        raise GuardRefusal("G2", f"execution request signature refused: {error}") from error
    # A record stamped this far ahead of its stored_at fails to load, which
    # would block the proposal and its cluster.
    ahead = context.now - context.store.database_time()
    if ahead > CLOCK_MARGIN:
        raise GuardRefusal(
            "G2", f"the executor's clock is {int(ahead.total_seconds())} seconds ahead of the store's"
        )


def g3_published_proposal(context: GuardContext, proposal_id: UUID) -> PublishedProposal:
    # Loading verifies the publication and its nested digests, and the
    # contract checks the proposal_created event and CWA trace against it.
    return _published_or_refuse(context, proposal_id)


def g4_succeeded_command(published: PublishedProposal) -> None:
    if published.command.status is not CommandState.SUCCEEDED:
        raise GuardRefusal("G4", "the proposal's command did not succeed")


def g5_approvals(context: GuardContext, proposal: Proposal, action: str) -> list[ApprovalRecord]:
    try:
        records = context.store.select("approvals", proposal_id=proposal.proposal_id)
    except RecordInvalid as error:
        raise GuardRefusal("G5", f"an approval is invalid: {error}") from error
    if any(record.decision is ApprovalDecision.REJECTED for record in records):
        raise GuardRefusal("G5", "the proposal is rejected")
    for record in records:
        try:
            require_not_future(record.created_at, now=context.now)
        except SignatureError as error:
            raise GuardRefusal("G5", f"approval {record.approval_id}: {error}") from error
    counting = [
        record
        for record in records
        if record.proposal_digest == proposal.proposal_digest
        and record.action_digest == action
        and record.expires_at is not None
        and record.expires_at > context.now
        and record.principal != context.executor
    ]
    approvers = {record.principal for record in counting}
    needed = required_approvers(proposal)
    if len(approvers) < needed:
        raise GuardRefusal(
            "G5",
            f"{needed} distinct approvers other than the executor are required; "
            f"{len(approvers)} unexpired approval(s) count",
        )
    return counting


def _executions(context: GuardContext, **equal: object) -> list[ExecutionRecord]:
    try:
        return context.store.select("executions", **equal)
    except RecordInvalid as error:
        raise GuardRefusal("G6", f"an execution record is invalid: {error}") from error


def g6_one_execution(
    context: GuardContext,
    proposal: Proposal,
    approvals: list[ApprovalRecord],
) -> None:
    executions = _executions(context, proposal_id=proposal.proposal_id)
    # Staleness is G7's; here the state must be approved and unexecuted.
    try:
        state = derive_state(
            proposal, approvals, git_stale=False, published=True,
            now=context.now, executions=executions,
        )
    except RecordInvalid as error:
        raise GuardRefusal("G6", f"an execution record is invalid: {error}") from error
    if state is not DerivedState.APPROVED:
        raise GuardRefusal("G6", f"the proposal is {state.value}, not approved")
    if context.store.select("commands", idempotency_key=context.command.idempotency_key):
        raise GuardRefusal("G6", "the idempotency key is already used")
    if context.hub_effect(proposal.proposal_id):
        raise GuardRefusal("G6", "the hub already holds a PipelineRun for this proposal")


def g7_not_stale(context: GuardContext, proposal: Proposal) -> None:
    if context.git_stale(proposal):
        raise GuardRefusal("G7", "fleet-clusters changed the cluster since the base commit")


def g8_regenerates(context: GuardContext, proposal: Proposal, action: str) -> None:
    try:
        regenerated = context.regenerate(proposal)
    except Refusal as refusal:
        raise GuardRefusal("G8", f"regeneration refused: {refusal}") from refusal
    if action_digest(regenerated) != action:
        raise GuardRefusal("G8", "the regenerated action differs from the approved one")


def g9_current_contract(proposal: Proposal) -> None:
    try:
        Proposal.model_validate(proposal.model_dump(mode="json"))
    except ValidationError as error:
        raise GuardRefusal("G9", f"the proposal breaks the current contract: {error}") from error


def g10_adapter(context: GuardContext, proposal: Proposal) -> ExecutionAdapter:
    default = (
        AdapterName.GIT_HANDOFF
        if proposal.proposal_type in GIT_PROPOSAL_TYPES
        else AdapterName.TEKTON
    )
    name = (
        AdapterName.RECORDING
        if AdapterName.RECORDING in context.enabled_adapters
        else default
    )
    if name not in context.enabled_adapters:
        raise GuardRefusal("G10", f"adapter {name.value} is not enabled")
    adapter = context.adapters.get(name)
    if adapter is None:
        raise GuardRefusal("G10", f"adapter {name.value} is not available")
    if "eventlistener" in getattr(adapter, "target", "").lower():
        raise GuardRefusal("G10", f"adapter {name.value} targets the EventListener")
    if name is AdapterName.RECORDING and context.backend != "sqlite":
        # It writes nothing outside Aura, so on a shared store it would
        # consume the proposal with no effect.
        raise GuardRefusal(
            "G10", "the recording adapter is for development and tests, on SQLite only"
        )
    if name is not AdapterName.RECORDING and (
        context.backend != "postgresql"
        or context.deployment_id != str(context.policy.deployment_id)
    ):
        raise GuardRefusal(
            "G10", f"adapter {name.value} requires the store the hub names"
        )
    return adapter


def g11_cluster_and_rate(context: GuardContext, proposal: Proposal) -> None:
    by_execution: dict[UUID, list[ExecutionRecord]] = {}
    for record in _executions(context, cluster_name=proposal.cluster_name):
        by_execution.setdefault(record.execution_id, []).append(record)
    for records in by_execution.values():
        if execution_state(records) in OPEN_STATES:
            raise GuardRefusal(
                "G11", f"another execution for cluster {proposal.cluster_name} is open"
            )
    if context.store.dispatches_in_last_hour() >= context.hourly_limit:
        raise GuardRefusal(
            "G11", f"{context.hourly_limit} executions were dispatched in the past hour"
        )


def g12_preflight(context: GuardContext, adapter: ExecutionAdapter) -> None:
    problem = context.preflight(adapter.name)
    if problem is not None:
        raise GuardRefusal("G12", f"preflight failed: {problem}")


__all__ = [
    "DEFAULT_HOURLY_LIMIT",
    "Cleared",
    "GuardContext",
    "GuardRefusal",
    "check",
]
