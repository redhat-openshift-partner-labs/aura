"""`execute`: dispatch one approved proposal through the guard (task 3.5).

Write order (Phase 3 Guard section): `command_received`, then the guard;
`execution_started`, then the signed `dispatching` record in a locked
transaction that rechecks G5, G6, and G11; the adapter call; then
`execution_finished`, and the result record with the terminal record.
A dry run stops after the guard. Only this module calls an adapter's write
method (I10).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from uuid import UUID, uuid5

from aura.adapters import AdapterOutcomeUnknown, AdapterRequest, AdapterResult
from aura.contracts import AuditEventType
from aura.execution.commands import Chain, CommandFailure
from aura.execution.guard import (
    Cleared,
    GuardContext,
    GuardRefusal,
    check,
    g5_approvals,
    g6_one_execution,
    g11_cluster_and_rate,
)
from aura.execution.records import ExecutionPhase, ExecutionRecord
from aura.execution.store import RecordConflict, StoreError, StoreOutcomeUnknown
from aura.redaction import redact, redact_text
from aura.signing import EXECUTE, SignatureError, verify

_RECOVERY = (
    "; the execution is awaiting_operator: run `aura execution status` to "
    "resolve it"
)
_UNSIGNED = "-----BEGIN SSH SIGNATURE-----\nAAAA\n-----END SSH SIGNATURE-----\n"
_RESULT_PHASE = {
    "git_handoff": ExecutionPhase.HANDED_OFF,
}


class AwaitingOperator(CommandFailure):
    """The dispatch may have happened; `execution status` resolves it."""


def execute_step(
    proposal_id: UUID,
    make_context: Callable[[Chain], GuardContext],
    *,
    dry_run: bool,
) -> Callable[[Chain], str]:
    def step(chain: Chain) -> str:
        context = make_context(chain)
        cleared = check(context, proposal_id)
        execution_id = uuid5(chain.command.command_id, "execution")
        request = AdapterRequest(
            request_id=uuid5(execution_id, "request"),
            command_id=chain.command.command_id,
            execution_id=execution_id,
            proposal_id=proposal_id,
            proposal_digest=cleared.published.proposal_digest,
            action_digest=cleared.action_digest,
            adapter=cleared.adapter.name,
            input={"proposal": cleared.published.proposal.model_dump(mode="json")},
        )
        if dry_run:
            return request.model_dump_json(indent=2)
        _dispatch(chain, context, cleared, request)
        # From here an external effect may exist; every failure says so.
        chain.recovery = _RECOVERY
        try:
            result = cleared.adapter.write(request)
        except AdapterOutcomeUnknown as error:
            raise AwaitingOperator(
                f"adapter {request.adapter.value} did not report its result: {error}"
            ) from error
        except Exception as error:
            # Other exception text may carry secret values (I6).
            raise AwaitingOperator(
                f"adapter {request.adapter.value} did not report its result "
                f"({type(error).__name__})"
            ) from error
        if (result.request_id, result.execution_id, result.adapter) != (
            request.request_id, request.execution_id, request.adapter,
        ):
            raise AwaitingOperator(
                f"adapter {request.adapter.value} reported a result for another request"
            )
        record = _record(chain, context, cleared, request, result)
        if not result.success:
            raise CommandFailure(
                f"adapter {request.adapter.value} failed: {record.failure_reason}"
            )
        return record.model_dump_json(indent=2)

    return step


def _dispatch(
    chain: Chain,
    context: GuardContext,
    cleared: Cleared,
    request: AdapterRequest,
) -> None:
    record_id = uuid5(request.execution_id, ExecutionPhase.DISPATCHING.value)
    event = chain.event(
        AuditEventType.EXECUTION_STARTED,
        {
            "record_id": str(record_id),
            "execution_id": str(request.execution_id),
            "proposal_id": str(request.proposal_id),
            "action_digest": request.action_digest,
            "adapter": request.adapter.value,
        },
        subject_id=record_id,
    )
    dispatching = _signed_record(
        context,
        cleared,
        request,
        record_id=record_id,
        phase=ExecutionPhase.DISPATCHING,
        audit_event_digest=event.event_digest,
        adapter_timeout_seconds=context.adapter_timeout_seconds,
    )
    chain.store_event(event)

    def recheck(reader) -> None:
        # Another dispatch may have committed, and an approval may have
        # expired, since the guard ran. The hub was read by the guard; the
        # recheck reads only the store, inside the transaction.
        rechecked = replace(
            context, store=reader, now=chain.clock(), hub_effect=lambda proposal_id: False
        )
        proposal = cleared.published.proposal
        approvals = g5_approvals(rechecked, proposal, cleared.action_digest)
        g6_one_execution(rechecked, proposal, approvals)
        g11_cluster_and_rate(rechecked, proposal)

    try:
        chain.store.exclusive("executions", dispatching, recheck)
    except RecordConflict as error:
        # Another dispatch of the proposal or its cluster committed first.
        raise GuardRefusal("G6", f"a concurrent dispatch committed first: {error}") from error
    except StoreOutcomeUnknown as error:
        chain.recovery = _RECOVERY
        raise AwaitingOperator(str(error)) from error


def _record(
    chain: Chain,
    context: GuardContext,
    cleared: Cleared,
    request: AdapterRequest,
    result: AdapterResult,
) -> ExecutionRecord:
    phase = (
        _RESULT_PHASE.get(request.adapter.value, ExecutionPhase.SUBMITTED)
        if result.success
        else ExecutionPhase.FAILED
    )
    record_id = uuid5(request.execution_id, phase.value)
    event = chain.event(
        AuditEventType.EXECUTION_FINISHED,
        {
            "record_id": str(record_id),
            "execution_id": str(request.execution_id),
            "phase": phase.value,
            "external_result": _redacted(result.external_result),
        },
        subject_id=record_id,
    )
    record = _signed_record(
        context,
        cleared,
        request,
        record_id=record_id,
        phase=phase,
        audit_event_digest=event.event_digest,
        # Stamped after the call, which may have run for minutes.
        created_at=event.occurred_at,
        external_result=_redacted(result.external_result) if result.success else None,
        failure_reason=None if result.success else redact_text(str(result.error)),
    )
    chain.store_event(event)
    # An outcome, not an actionable artifact: stored at once, so it stands
    # whatever happens to the terminal record.
    chain.store.insert("executions", record)
    return record


def _redacted(external_result: dict[str, str]) -> dict[str, str]:
    """An adapter's result with secret-named values redacted (I6)."""

    return {name: str(value) for name, value in redact(external_result).value.items()}


def _signed_record(
    context: GuardContext,
    cleared: Cleared,
    request: AdapterRequest,
    **fields: object,
) -> ExecutionRecord:
    proposal = cleared.published.proposal
    values = {
        "execution_id": request.execution_id,
        "command_id": request.command_id,
        "proposal_id": request.proposal_id,
        "proposal_digest": request.proposal_digest,
        "action_digest": request.action_digest,
        "proposal_type": proposal.proposal_type,
        "cluster_name": proposal.cluster_name,
        "approval_ids": [approval.approval_id for approval in cleared.approvals],
        "adapter": request.adapter,
        "adapter_version": cleared.adapter.version,
        "rollback": proposal.rollback,
        "principal": context.executor,
        "created_at": context.now,
        **fields,
    }
    draft = ExecutionRecord.model_validate({**values, "signature": _UNSIGNED})
    payload = draft.signed_payload()
    try:
        signature = context.sign(payload)
        verify(
            payload, signature, principal=context.executor, namespace=EXECUTE,
            signers=context.policy.allowed_signers, at=context.now,
        )
    except SignatureError as error:
        raise StoreError(f"execution record signature refused: {error}") from error
    return ExecutionRecord.model_validate({**values, "signature": signature})


__all__ = ["AwaitingOperator", "execute_step"]
