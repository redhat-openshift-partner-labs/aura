"""`execution status` and `execution confirm` (Phase 3 task 4.2).

`execution status` reads a proposal's execution and, for a Tekton
execution, the PipelineRun named for the proposal, through `aura-readonly`.
Without a signer it reports what it finds. With an `aura-execute` signer it
stores one observation per run read and a result record when the
highest-ranked phase changes, each after its `execution_observed` event,
and resolves an `awaiting_operator` execution: `submitted` when the run
exists, else `not_dispatched` once the dispatch is older than the adapter
timeout plus five minutes, by `timeout_margin` inside the hub's retention
period and otherwise only by a `manual` resolution that names another
principal's confirmation. It calls no adapter write method (I10).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid5

from pydantic import BaseModel, ValidationError

from aura.adapters import AdapterName
from aura.collectors.hub_policy import HubPolicy
from aura.contracts import AuditEventType
from aura.execution.commands import Chain, CommandFailure, Refusal
from aura.execution.derived import DerivedState, execution_state
from aura.execution.records import (
    PIPELINE_NAMESPACE,
    TERMINAL_PHASES,
    ExecutionPhase,
    ExecutionRecord,
    PipelineRunObservation,
    Resolution,
    ResolutionConfirmation,
)
from aura.execution.store import RecordNotFound, StoreError
from aura.execution.tekton import EXECUTION_ANNOTATION, run_name
from aura.signing import APPROVE, CLOCK_MARGIN, EXECUTE, SignatureError, verify

_UNSIGNED = "-----BEGIN SSH SIGNATURE-----\nAAAA\n-----END SSH SIGNATURE-----\n"
_RANK = {
    ExecutionPhase.DISPATCHING: 0,
    ExecutionPhase.SUBMITTED: 1,
    ExecutionPhase.RUNNING: 2,
}
_SUCCEEDED = {
    "True": ExecutionPhase.SUCCEEDED,
    "False": ExecutionPhase.FAILED,
    "Unknown": ExecutionPhase.RUNNING,
}


@dataclass(frozen=True)
class Signer:
    principal: str
    sign: Callable[[bytes], str]


@dataclass(frozen=True)
class StatusContext:
    """Everything `execution status` reads; tests supply each part."""

    policy: HubPolicy
    # The PipelineRuns in openshift-pipelines, read through aura-readonly.
    runs: Callable[[], list[dict[str, Any]]]
    adapter_timeout: timedelta
    # Present with --record: the aura-execute signer of stored records.
    signer: Signer | None
    confirmation_id: UUID | None = None


def status_step(
    proposal_id: UUID,
    make_context: Callable[[Chain], StatusContext],
) -> Callable[[Chain], str]:
    def step(chain: Chain) -> str:
        context = make_context(chain)
        store = chain.store.with_signers(context.policy.allowed_signers)
        if context.signer is not None:
            _require_signer(chain, store, context.signer, context.policy, EXECUTE)
        dispatch, records = _execution(store, proposal_id)
        state = execution_state(records)
        report: dict[str, Any] = {
            "proposal_id": str(proposal_id),
            "execution_id": str(dispatch.execution_id),
            "adapter": dispatch.adapter.value,
            "state": state.value,
            "pipeline_run": None,
            "recorded": [],
            "last_observation": None,
        }
        if dispatch.adapter is not AdapterName.TEKTON:
            report["note"] = f"execution status reads no external system for {dispatch.adapter.value}"
            return json.dumps(report, indent=2)
        recorder = _Recorder(chain, context, dispatch)
        observations = sorted(
            store.select("pipeline_run_observations", execution_id=dispatch.execution_id),
            key=lambda observation: observation.observed_at,
        )
        if observations:
            report["last_observation"] = observations[-1].model_dump(mode="json")
        run = _find_run(context.runs(), proposal_id)
        if run is not None:
            _observed(recorder, report, run, state, _past_margin(store, dispatch, context))
        elif observations:
            # Pruned after it was observed: the last observation stands, and
            # a pruned run is never recorded not_dispatched (P3.21).
            report["note"] = "the PipelineRun is gone; its last observation is shown"
        elif state is DerivedState.AWAITING_OPERATOR:
            _unfound(recorder, report, store, dispatch, context)
        else:
            report["note"] = "the PipelineRun was not found"
        return json.dumps(report, indent=2)

    return step


def _execution(store, proposal_id: UUID) -> tuple[ExecutionRecord, list[ExecutionRecord]]:
    records = store.select("executions", proposal_id=proposal_id)
    dispatches = [r for r in records if r.phase is ExecutionPhase.DISPATCHING]
    if not dispatches:
        raise Refusal(f"proposal {proposal_id} has no execution")
    # The store allows one dispatching record per proposal.
    (dispatch,) = dispatches
    return dispatch, [r for r in records if r.execution_id == dispatch.execution_id]


def _find_run(runs: list[dict[str, Any]], proposal_id: UUID) -> dict[str, Any] | None:
    name = run_name(proposal_id)
    found = [run for run in runs if (run.get("metadata") or {}).get("name") == name]
    return found[0] if found else None


def _observed(
    recorder: "_Recorder", report: dict[str, Any], run: dict[str, Any], state: DerivedState,
    past_margin: bool,
) -> None:
    metadata = run.get("metadata") or {}
    owner = (metadata.get("annotations") or {}).get(EXECUTION_ANNOTATION)
    report["pipeline_run"] = {"name": metadata.get("name"), "uid": metadata.get("uid")}
    if owner != str(recorder.dispatch.execution_id):
        # Created outside this execution and possibly running: the
        # execution stays open, so G11 keeps its cluster closed (operator
        # decision). An observation names its execution, so none is stored.
        report["note"] = (
            "a PipelineRun of this name was created by another execution; "
            "the execution stays open until an operator resolves the run"
        )
        return
    observation = recorder.observation(run)
    report["pipeline_run"]["succeeded_status"] = observation["succeeded_status"]
    report["last_observation"] = observation
    if state is DerivedState.AWAITING_OPERATOR and not past_margin:
        # The executor's call may still be storing its own result.
        report["note"] = "the PipelineRun exists; submitted is recorded once the timeout margin has passed"
        return
    if state is DerivedState.AWAITING_OPERATOR:
        recorder.result(report, ExecutionPhase.SUBMITTED, external_result={
            "pipeline_run": str(metadata["name"]),
            "uid": str(metadata["uid"]),
            "namespace": PIPELINE_NAMESPACE,
        })
        state = DerivedState.SUBMITTED
    if not observation["started"]:
        return
    phase = _SUCCEEDED[observation["succeeded_status"]]
    current = ExecutionPhase(state.value)
    if current in TERMINAL_PHASES or _RANK.get(phase, 3) <= _RANK[current]:
        return
    if phase is ExecutionPhase.FAILED:
        recorder.result(report, phase, failure_reason=f"the PipelineRun failed: {observation['succeeded_reason'] or 'no reason'}")
    else:
        recorder.result(report, phase)


def _margin(dispatch: ExecutionRecord, context: StatusContext) -> timedelta:
    """The dispatch's own adapter timeout plus five minutes; the reader's
    configured timeout only for a record stored before the field existed."""

    timeout = (
        timedelta(seconds=dispatch.adapter_timeout_seconds)
        if dispatch.adapter_timeout_seconds is not None
        else context.adapter_timeout
    )
    return timeout + CLOCK_MARGIN


def _age(store, dispatch: ExecutionRecord) -> timedelta:
    return store.database_time() - store.stored_at("executions", record_id=dispatch.record_id)


def _past_margin(store, dispatch: ExecutionRecord, context: StatusContext) -> bool:
    return _age(store, dispatch) >= _margin(dispatch, context)


def _require_signer(chain: Chain, store, signer: "Signer", policy: HubPolicy, namespace: str) -> None:
    """Refuse a key the hub does not list for namespace, and a clock so far
    ahead of the store's that the signed records would fail to load."""

    payload = json.dumps({"command_id": str(chain.command.command_id), "purpose": namespace}).encode()
    try:
        verify(payload, signer.sign(payload), principal=signer.principal, namespace=namespace,
               signers=policy.allowed_signers, at=chain.clock())
    except SignatureError as error:
        raise Refusal(f"the signing key is refused for {namespace}: {error}") from error
    ahead = chain.clock() - store.database_time()
    if ahead > CLOCK_MARGIN:
        raise Refusal(f"this clock is {int(ahead.total_seconds())} seconds ahead of the store's")


def _unfound(recorder: "_Recorder", report: dict[str, Any], store, dispatch: ExecutionRecord, context: StatusContext) -> None:
    age = _age(store, dispatch)
    margin = _margin(dispatch, context)
    if age < margin:
        report["note"] = (
            f"no PipelineRun yet; the dispatch is {int(age.total_seconds())} seconds old, "
            f"and not_dispatched needs {int(margin.total_seconds())}"
        )
        return
    retention = context.policy.pipeline_run_retention
    if retention is not None and age < retention:
        recorder.result(report, ExecutionPhase.NOT_DISPATCHED, resolution=Resolution.TIMEOUT_MARGIN)
        return
    if context.confirmation_id is None:
        report["note"] = (
            "a pruned run cannot be told from none: record not_dispatched with "
            "`aura execution confirm`, then `execution status --record --confirmation-id`"
        )
        return
    confirmation = _confirmation(store, context, dispatch)
    recorder.result(
        report, ExecutionPhase.NOT_DISPATCHED,
        resolution=Resolution.MANUAL,
        confirmation_id=confirmation.confirmation_id,
        failure_reason=confirmation.statement,
    )


def _confirmation(store, context: StatusContext, dispatch: ExecutionRecord) -> ResolutionConfirmation:
    try:
        confirmation = store.get("resolution_confirmations", confirmation_id=context.confirmation_id)
    except RecordNotFound as error:
        raise Refusal(f"confirmation {context.confirmation_id} is not stored") from error
    if confirmation.execution_id != dispatch.execution_id:
        raise Refusal(f"confirmation {context.confirmation_id} is for another execution")
    if context.signer is not None and confirmation.principal == context.signer.principal:
        raise Refusal("a manual resolution needs another principal's confirmation")
    return confirmation


@dataclass
class _Recorder:
    """Stores observations and result records when a signer is present,
    and lists what it stored in the report."""

    chain: Chain
    context: StatusContext
    dispatch: ExecutionRecord

    def observation(self, run: dict[str, Any]) -> dict[str, Any]:
        metadata = run.get("metadata") or {}
        status = run.get("status") or {}
        condition = next(
            (c for c in status.get("conditions") or [] if c.get("type") == "Succeeded"), {}
        )
        observation_id = uuid5(self.chain.command.command_id, "observation")
        fields: dict[str, Any] = {
            "observation_id": observation_id,
            "execution_id": self.dispatch.execution_id,
            "command_id": self.chain.command.command_id,
            "name": metadata.get("name"),
            "uid": metadata.get("uid"),
            "namespace": metadata.get("namespace"),
            # A run without the condition has not started.
            "succeeded_status": condition.get("status", "Unknown"),
            "started": bool(condition),
            "succeeded_reason": condition.get("reason"),
            "start_time": status.get("startTime"),
            "completion_time": status.get("completionTime"),
            "observed_at": self.chain.clock(),
        }
        started = fields.pop("started")
        # Validated whether or not it is stored, so a malformed run fails.
        try:
            draft = PipelineRunObservation.model_validate(
                {**fields, "audit_event_digest": "0" * 64, "principal": "unsigned", "signature": _UNSIGNED}
            )
        except ValidationError as error:
            raise CommandFailure(
                f"PipelineRun {fields['name']} has an unreadable status: {error.errors()[0]['msg']}"
            ) from error
        observed = draft.model_dump(
            mode="json", exclude={"audit_event_digest", "principal", "signature", "observation_digest"}
        )
        observed["started"] = started
        signer = self.context.signer
        if signer is None:
            return observed
        event = self.chain.event(
            AuditEventType.EXECUTION_OBSERVED,
            {"observation_id": str(observation_id), "execution_id": str(self.dispatch.execution_id),
             "succeeded_status": fields["succeeded_status"]},
            subject_id=observation_id,
        )
        record = _signed(
            PipelineRunObservation,
            {**fields, "audit_event_digest": event.event_digest, "principal": signer.principal},
            signer, self.context.policy, EXECUTE, fields["observed_at"],
        )
        self.chain.store_event(event)
        self.chain.store.insert("pipeline_run_observations", record)
        return observed

    def result(self, report: dict[str, Any], phase: ExecutionPhase, **fields: Any) -> None:
        signer = self.context.signer
        if signer is None:
            report.setdefault("would_record", []).append(phase.value)
            return
        report["recorded"].append(phase.value)
        dispatch = self.dispatch
        record_id = uuid5(dispatch.execution_id, phase.value)
        event = self.chain.event(
            AuditEventType.EXECUTION_OBSERVED,
            {"record_id": str(record_id), "execution_id": str(dispatch.execution_id), "phase": phase.value},
            subject_id=record_id,
        )
        created_at = self.chain.clock()
        values = {
            **dispatch.model_dump(
                mode="json",
                include={
                    "execution_id", "proposal_id", "proposal_digest", "action_digest",
                    "proposal_type", "cluster_name", "approval_ids", "adapter",
                    "adapter_version", "rollback",
                },
            ),
            "record_id": record_id,
            "command_id": self.chain.command.command_id,
            "phase": phase,
            "principal": signer.principal,
            "created_at": created_at,
            "audit_event_digest": event.event_digest,
            **fields,
        }
        record = _signed(ExecutionRecord, values, signer, self.context.policy, EXECUTE, created_at)
        self.chain.store_event(event)
        self.chain.store.insert("executions", record)


def _signed(model: type[BaseModel], fields: dict[str, Any], signer: Signer, policy: HubPolicy, namespace: str, at: datetime):
    """Sign a record and verify it as load will, so a signature the hub
    list refuses is never stored."""

    try:
        draft = model.model_validate({**fields, "signature": _UNSIGNED})
    except ValidationError as error:
        raise CommandFailure(f"the {model.__name__} would be invalid: {error.errors()[0]['msg']}") from error
    payload = draft.signed_payload()
    try:
        signature = signer.sign(payload)
        verify(payload, signature, principal=signer.principal, namespace=namespace,
               signers=policy.allowed_signers, at=at)
    except SignatureError as error:
        raise StoreError(f"{model.__name__} signature refused: {error}") from error
    return model.model_validate({**fields, "signature": signature})


def confirm_step(
    proposal_id: UUID,
    statement: str,
    signer: Signer,
    read_policy: Callable[[], HubPolicy],
) -> Callable[[Chain], str]:
    """Store an approver's signed confirmation that an awaiting_operator
    execution made no external effect."""

    def step(chain: Chain) -> str:
        policy = read_policy()
        store = chain.store.with_signers(policy.allowed_signers)
        _require_signer(chain, store, signer, policy, APPROVE)
        dispatch, records = _execution(store, proposal_id)
        state = execution_state(records)
        if state is not DerivedState.AWAITING_OPERATOR:
            raise Refusal(f"the execution is {state.value}, not awaiting_operator")
        confirmation_id = uuid5(chain.command.command_id, "confirmation")
        created_at = chain.clock()
        fields = {
            "confirmation_id": confirmation_id,
            "execution_id": dispatch.execution_id,
            "command_id": chain.command.command_id,
            "statement": statement,
            "principal": signer.principal,
            "created_at": created_at,
        }
        event = chain.event(
            AuditEventType.RESOLUTION_CONFIRMED,
            {"confirmation_id": str(confirmation_id), "execution_id": str(dispatch.execution_id)},
            subject_id=confirmation_id,
        )
        record = _signed(
            ResolutionConfirmation, {**fields, "audit_event_digest": event.event_digest},
            signer, policy, APPROVE, created_at,
        )
        chain.store_event(event)
        chain.store_artifact("resolution_confirmations", record)
        return record.model_dump_json(indent=2)

    return step


__all__ = ["Signer", "StatusContext", "confirm_step", "status_step"]
