from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import TypeVar
from uuid import UUID, uuid4, uuid5

from pydantic_core import from_json

from pydantic import ValidationError

from aura.audit_store import (
    AuditEventAlreadyStored,
    AuditEventNotFound,
    JsonAuditEventStore,
)
from aura.authorization import OperatorRole
from aura.command_store import (
    CommandAlreadyStored,
    CommandNotTerminal,
    CommandNotFound,
    CommandRecordUnreadable,
    JsonCommandStore,
)
from aura.collectors.cluster_inventory import ClusterInventoryCollector
from aura.collectors.cluster_state import ClusterStateCollector
from aura.collectors.git_inventory import GitInventoryCollector
from aura.collectors.git_inventory import GitInventoryError
from aura.collectors.git_staleness import (
    GitProposalStaleness,
    ProposalRecordUnreadable,
    ProposalStalenessCollector,
)
from aura.collectors.oc_cli import OpenShiftCLIError, oc_call_budget
from aura.collectors.openshift_cli_client import OpenShiftCLIInventoryClient
from aura.collectors.openshift_cli_tekton_client import (
    OpenShiftCLITektonClient,
)
from aura.collectors.openshift_cli_pipeline_client import (
    OpenShiftCLIPipelineClient,
)
from aura.collectors.openshift_inventory import OpenShiftInventoryCollector
from aura.collectors.pipeline_definitions import (
    PipelineDefinitionCollector,
    PipelineDefinitionError,
)
from aura.collectors.rerun_evidence import RerunEvidenceCollector
from aura.collectors.scaffold_evidence import ScaffoldEvidenceCollector
from aura.collectors.tekton_inventory import TektonInventoryCollector
from aura.collectors.workflow_inventory import WorkflowInventoryCollector
from aura.cwa_store import (
    CWASnapshotAlreadyStored,
    CWATraceAlreadyStored,
    JsonCWASnapshotStore,
    JsonCWATraceStore,
)
from aura.contracts import (
    ARCHIVE_SUFFIX,
    AuditEvent,
    AuditEventType,
    CommandEnvelope,
    CommandState,
    CommandType,
    EvidenceSnapshot,
)
from aura.cwa import CWASnapshot, CWATrace
from aura.diagnosis import (
    DiagnosisAnswer,
    DiagnosisEvidenceUnavailable,
    DiagnosisModel,
    DiagnosisRecord,
)
from aura.diagnosis_store import (
    DiagnosisAlreadyStored,
    JsonDiagnosisStore,
)
from aura.evidence_store import (
    EvidenceSnapshotAlreadyStored,
    JsonEvidenceSnapshotStore,
)
from aura.proposal_store import (
    JsonProposalStore,
    ProposalAlreadyStored,
    ProposalNotFound,
)
from aura.proposals import (
    CLUSTER_NAME_PATTERN,
    SCAFFOLD_OPERATOR_FLAGS,
    Proposal,
    ProposalType,
)
from aura.scaffold import (
    ScaffoldCheckoutError,
    ScaffoldRequest,
    ScaffoldRunError,
)
from aura.scaffold_runner import (
    GitDefinitionCheck,
    GitPatchCheck,
    SubprocessScaffoldRunner,
    kustomize_version,
)
from aura.services.cwa import (
    CWAAssemblyResult,
    CWAAssemblyPolicy,
    CWAAssemblyRequest,
    CWAAssembler,
    InvalidCWAAssembly,
)
from aura.services.diagnosis import DiagnosisService
from aura.services.evidence import (
    EvidenceCommandResult,
    EvidenceCommandService,
)
from aura.services.cluster_state import (
    ClusterStateDiagnosisModel,
    ClusterStateService,
    InvalidClusterStateCommand,
    UnauthorizedClusterStateCommand,
)
from aura.services.cluster_edit import ClusterEditProposalGenerator
from aura.services.rerun import PostProvisionRerunGenerator
from aura.services.scaffold import ScaffoldProposalGenerator
from aura.services.proposal import (
    DeprovisionProposalGenerator,
    InvalidProposalCommand,
    PipelineProposalService,
    ProposalGenerationError,
    ProposalRefused,
    ProposalService,
    ProposalStatusService,
    UnauthorizedProposalCommand,
)
from aura.services.inventory import EvidenceCollector, InventoryService
from aura.services.workflow import WorkflowService
from aura.state import CommandStateMachine, InvalidCommandTransition


T = TypeVar("T")


_FLEET_CLUSTERS = (
    "--fleet-clusters",
    "Path to the fleet-clusters Git checkout",
)
_KUBECONFIG = (
    "--openshift-kubeconfig",
    "Path to a read-only OpenShift kubeconfig",
)
_SNAPSHOT_DIR = (
    "--snapshot-dir",
    "Directory in which to store the evidence snapshot",
)
_AUDIT_DIR = ("--audit-dir", "Directory in which to store audit events")
_CWA_DIR = (
    "--cwa-dir",
    "Directory in which to store CWA snapshots and traces",
)
_DIAGNOSIS_DIR = (
    "--diagnosis-dir",
    "Directory in which to store diagnosis responses",
)
_PROPOSAL_DIR = ("--proposal-dir", "Directory in which to store proposals")


def _add_path_arguments(
    parser: argparse.ArgumentParser,
    *arguments: tuple[str, str],
    required: bool = False,
) -> None:
    for flag, help_text in arguments:
        parser.add_argument(
            flag,
            type=Path,
            required=required,
            help=help_text,
        )


def _add_operator_arguments(
    parser: argparse.ArgumentParser,
    query: str,
    *,
    query_help: str = "Operator query used for CWA assembly",
) -> None:
    parser.add_argument("--query", default=query, help=query_help)
    parser.add_argument(
        "--command-dir",
        type=Path,
        required=True,
        help="Directory for completed idempotency-key records",
    )
    parser.add_argument("--operator", required=True)
    parser.add_argument("--idempotency-key", required=True)
    parser.add_argument(
        "--role",
        action="append",
        choices=[role.value for role in OperatorRole],
        help="Operator role; may be repeated; defaults to viewer",
    )


def _add_inventory_list_arguments(
    parser: argparse.ArgumentParser,
) -> None:
    parser.add_argument(
        "--source",
        choices=["git", "openshift", "combined"],
        default="git",
        help="Inventory source to query",
    )
    _add_path_arguments(
        parser,
        _FLEET_CLUSTERS,
        _KUBECONFIG,
        _SNAPSHOT_DIR,
        _AUDIT_DIR,
        _CWA_DIR,
        _DIAGNOSIS_DIR,
    )
    _add_operator_arguments(parser, "list inventory")


def _add_workflow_inspect_arguments(
    parser: argparse.ArgumentParser,
) -> None:
    _add_path_arguments(parser, _KUBECONFIG, required=True)
    _add_path_arguments(parser, _FLEET_CLUSTERS, _SNAPSHOT_DIR, _AUDIT_DIR)
    parser.add_argument(
        "--namespace",
        required=False,
        help="Tekton namespace to inspect; omit for all namespaces",
    )
    _add_operator_arguments(
        parser,
        "inspect workflows",
        query_help="Operator query recorded with the command",
    )


def _add_diagnose_state_arguments(
    parser: argparse.ArgumentParser,
) -> None:
    parser.add_argument("--cluster", required=True, help="Cluster name")
    _add_path_arguments(parser, _FLEET_CLUSTERS, _KUBECONFIG, required=True)
    parser.add_argument(
        "--pipeline-run",
        required=False,
        help=(
            "PipelineRun name or UID; omit to select the latest run"
        ),
    )
    _add_path_arguments(
        parser,
        _SNAPSHOT_DIR,
        _AUDIT_DIR,
        _CWA_DIR,
        _DIAGNOSIS_DIR,
    )
    _add_operator_arguments(parser, "diagnose cluster state")


_GIT_PROPOSAL_GENERATORS = {
    ProposalType.DEPROVISION.value: DeprovisionProposalGenerator,
    ProposalType.CLUSTER_EDIT.value: ClusterEditProposalGenerator,
}
_RERUN = ProposalType.POST_PROVISION_RERUN.value
_SCAFFOLD = ProposalType.SCAFFOLD.value
ProposalGenerator = (
    DeprovisionProposalGenerator
    | ClusterEditProposalGenerator
    | PostProvisionRerunGenerator
    | ScaffoldProposalGenerator
)


def _proposal_generator(args: argparse.Namespace) -> ProposalGenerator:
    if args.proposal_type == _RERUN:
        return PostProvisionRerunGenerator()
    if args.proposal_type != _SCAFFOLD:
        return _GIT_PROPOSAL_GENERATORS[args.proposal_type](
            patch_check=GitPatchCheck(args.fleet_clusters)
        )
    return ScaffoldProposalGenerator(
        runner=SubprocessScaffoldRunner(
            fleet_clusters=args.fleet_clusters,
            scaffold=args.scaffold,
        ),
        definition_check=GitDefinitionCheck(args.fleet_clusters),
    )


def _scaffold_request(args: argparse.Namespace) -> ScaffoldRequest:
    """Build the request, with operator flags in allow-list order."""

    return ScaffoldRequest(
        cloud=args.cloud,
        cluster_name=args.cluster,
        region=args.region,
        tier=args.tier,
        options=tuple(
            (flag, value)
            for flag in SCAFFOLD_OPERATOR_FLAGS
            if (value := getattr(args, _flag_dest(flag))) is not None
        ),
    )


def _flag_dest(flag: str) -> str:
    return flag.removeprefix("--").replace("-", "_")


def _add_propose_arguments(
    parser: argparse.ArgumentParser,
    proposal_type: ProposalType,
    *,
    pipeline_name: str | None = None,
) -> None:
    parser.set_defaults(
        proposal_type=proposal_type.value,
        pipeline_name=pipeline_name,
    )
    parser.add_argument("--cluster", required=True, help="Cluster name")
    _add_path_arguments(
        parser,
        _FLEET_CLUSTERS,
        _KUBECONFIG,
        _SNAPSHOT_DIR,
        _AUDIT_DIR,
        _CWA_DIR,
        _PROPOSAL_DIR,
        required=True,
    )
    _add_operator_arguments(
        parser,
        f"propose {proposal_type.value.replace('_', ' ')}",
    )


def _add_proposal_status_arguments(
    parser: argparse.ArgumentParser,
) -> None:
    _add_path_arguments(parser, _FLEET_CLUSTERS, _PROPOSAL_DIR, required=True)
    parser.add_argument(
        "--proposal-id",
        action="append",
        type=UUID,
        default=[],
        help="Proposal ID to check; may be repeated; omit for all",
    )
    _add_path_arguments(parser, _SNAPSHOT_DIR, _AUDIT_DIR)
    _add_operator_arguments(
        parser,
        "check proposal staleness",
        query_help="Operator query recorded with the command",
    )


def _add_propose_parsers(
    command_parser: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    propose_parser = command_parser.add_parser("propose")
    propose_command_parser = propose_parser.add_subparsers(
        dest="propose_command",
        required=True,
    )
    _add_propose_arguments(
        propose_command_parser.add_parser("deprovision"),
        ProposalType.DEPROVISION,
    )
    _add_propose_arguments(
        propose_command_parser.add_parser("cluster-edit"),
        ProposalType.CLUSTER_EDIT,
        pipeline_name="post-provision",
    )
    rerun_parser = propose_command_parser.add_parser("post-provision-rerun")
    _add_propose_arguments(
        rerun_parser,
        ProposalType.POST_PROVISION_RERUN,
        pipeline_name="post-provision",
    )
    rerun_parser.add_argument(
        "--pipeline-run",
        required=False,
        help="post-provision PipelineRun to rerun; omit for the latest",
    )
    scaffold_parser = propose_command_parser.add_parser("scaffold")
    _add_propose_arguments(scaffold_parser, ProposalType.SCAFFOLD)
    scaffold_parser.add_argument(
        "--scaffold",
        type=Path,
        required=True,
        help="Path to a clean local fleet-cluster-scaffold checkout",
    )
    scaffold_parser.add_argument("--region", required=True, help="Region")
    scaffold_parser.add_argument("--tier", required=True, help="Tier")
    scaffold_parser.add_argument(
        "--cloud",
        default="aws",
        help="Cloud provider; only aws is supported",
    )
    for flag in SCAFFOLD_OPERATOR_FLAGS:
        scaffold_parser.add_argument(
            flag,
            dest=_flag_dest(flag),
            help=f"Passed to the scaffold as {flag}",
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aura")
    command_parser = parser.add_subparsers(dest="command", required=True)
    inventory_parser = command_parser.add_parser("inventory")
    inventory_command_parser = inventory_parser.add_subparsers(
        dest="inventory_command",
        required=True,
    )
    list_parser = inventory_command_parser.add_parser("list")
    _add_inventory_list_arguments(list_parser)

    workflow_parser = command_parser.add_parser("workflow")
    workflow_command_parser = workflow_parser.add_subparsers(
        dest="workflow_command",
        required=True,
    )
    inspect_parser = workflow_command_parser.add_parser("inspect")
    _add_workflow_inspect_arguments(inspect_parser)

    diagnose_parser = command_parser.add_parser("diagnose")
    diagnose_command_parser = diagnose_parser.add_subparsers(
        dest="diagnose_command",
        required=True,
    )
    state_parser = diagnose_command_parser.add_parser("state")
    _add_diagnose_state_arguments(state_parser)

    _add_propose_parsers(command_parser)

    proposal_parser = command_parser.add_parser("proposal")
    proposal_command_parser = proposal_parser.add_subparsers(
        dest="proposal_command",
        required=True,
    )
    status_parser = proposal_command_parser.add_parser("status")
    _add_proposal_status_arguments(status_parser)
    return parser


def _inventory_argument_error(args: argparse.Namespace) -> str | None:
    if args.source == "git" and args.fleet_clusters is None:
        return "--fleet-clusters is required for --source git"
    if args.source == "openshift" and args.openshift_kubeconfig is None:
        return "--openshift-kubeconfig is required for --source openshift"
    if args.source == "combined":
        if args.fleet_clusters is None:
            return "--fleet-clusters is required for --source combined"
        if args.openshift_kubeconfig is None:
            return (
                "--openshift-kubeconfig is required for "
                "--source combined"
            )
    if args.cwa_dir is not None and args.audit_dir is None:
        return "--audit-dir is required with --cwa-dir"
    if args.diagnosis_dir is not None and args.cwa_dir is None:
        return "--cwa-dir is required with --diagnosis-dir"
    return None


def _workflow_argument_error(args: argparse.Namespace) -> str | None:
    if args.openshift_kubeconfig is None:
        return "--openshift-kubeconfig is required"
    return None


def _diagnose_state_argument_error(args: argparse.Namespace) -> str | None:
    if args.cwa_dir is None:
        return "--cwa-dir is required for diagnose state"
    if args.cwa_dir is not None and args.audit_dir is None:
        return "--audit-dir is required with --cwa-dir"
    if args.diagnosis_dir is not None and args.cwa_dir is None:
        return "--cwa-dir is required with --diagnosis-dir"
    if args.pipeline_run is not None and not args.pipeline_run.strip():
        return "--pipeline-run must not be blank"
    return None


def _propose_argument_error(args: argparse.Namespace) -> str | None:
    if re.fullmatch(CLUSTER_NAME_PATTERN, args.cluster) is None:
        return f"--cluster must be a DNS-1123 label: {args.cluster}"
    if ARCHIVE_SUFFIX in args.cluster:
        return f"--cluster must not contain {ARCHIVE_SUFFIX}: {args.cluster}"
    pipeline_run = getattr(args, "pipeline_run", None)
    if pipeline_run is not None and not pipeline_run.strip():
        return "--pipeline-run must not be blank"
    return None


def _record_directory_argument_error(
    args: argparse.Namespace,
) -> str | None:
    directory_arguments = (
        ("command_dir", "--command-dir"),
        ("snapshot_dir", "--snapshot-dir"),
        ("audit_dir", "--audit-dir"),
        ("cwa_dir", "--cwa-dir"),
        ("diagnosis_dir", "--diagnosis-dir"),
        ("proposal_dir", "--proposal-dir"),
    )
    for attribute, flag in directory_arguments:
        directory = getattr(args, attribute, None)
        if directory is None:
            continue
        if directory.exists() and not directory.is_dir():
            return f"{flag} is not a usable directory: {directory}"
    return None


def _argument_error(args: argparse.Namespace) -> str | None:
    if not args.operator.strip():
        return "--operator must not be blank"
    if not args.idempotency_key.strip():
        return "--idempotency-key must not be blank"
    directory_error = _record_directory_argument_error(args)
    if directory_error is not None:
        return directory_error
    if args.command == "inventory":
        return _inventory_argument_error(args)
    if args.command == "workflow":
        return _workflow_argument_error(args)
    if args.command == "diagnose":
        return _diagnose_state_argument_error(args)
    if args.command == "propose":
        return _propose_argument_error(args)
    return None


def _store_terminal_command(
    args: argparse.Namespace,
    command: CommandEnvelope,
) -> str | None:
    return _record_store_error(
        lambda: JsonCommandStore(args.command_dir).save(command),
        (CommandAlreadyStored, CommandNotTerminal),
    )


def _record_store_error(
    action: Callable[[], Path],
    errors: tuple[type[Exception], ...],
) -> str | None:
    try:
        action()
    except (*errors, OSError) as error:
        return str(error)
    return None


def _duplicate_command_error(
    store: JsonCommandStore,
    command: CommandEnvelope,
) -> str | None:
    try:
        existing = store.load(command.idempotency_key)
    except CommandNotFound:
        return None
    except CommandRecordUnreadable as error:
        return str(error)
    if existing.request_digest != command.request_digest:
        return (
            "idempotency key belongs to a different request: "
            f"{command.idempotency_key}"
        )
    if existing.status is CommandState.REFUSED:
        return (
            "idempotency key already refused: "
            f"{command.idempotency_key}"
        )
    if existing.status is CommandState.FAILED:
        return (
            "idempotency key already failed: "
            f"{command.idempotency_key}"
        )
    return (
        "idempotency key already succeeded: "
        f"{command.idempotency_key}"
    )


def _execute_command(
    args: argparse.Namespace,
    command: CommandEnvelope,
    action: Callable[[], int],
    clock: Callable[[], datetime],
) -> int:
    store = JsonCommandStore(args.command_dir)
    try:
        with store.lock():
            duplicate_error = _duplicate_command_error(store, command)
            if duplicate_error is not None:
                print(f"aura: {duplicate_error}", file=sys.stderr)
                return 2
            try:
                with oc_call_budget():
                    return action()
            except Exception as error:
                return _fail_unexpected(args, command, error, clock)
    except OSError as error:
        print(f"aura: {error}", file=sys.stderr)
        return 2


def _fail_unexpected(
    args: argparse.Namespace,
    command: CommandEnvelope,
    error: Exception,
    clock: Callable[[], datetime],
) -> int:
    """Fail a command whose route raised an error it does not handle."""

    if isinstance(error, OSError):
        # An OS error names an errno and a path, as handled filesystem
        # errors do, so it is reported and stored whole (task 1.4).
        reason = message = f"filesystem error: {error}"
    else:
        # The exception text may carry secret values, so only its type is
        # stored; the full message goes to standard error alone (I6).
        reason = f"internal error: {type(error).__name__}"
        message = f"{reason}: {error}"
    try:
        JsonCommandStore(args.command_dir).load(command.idempotency_key)
    except CommandNotFound:
        return _fail_unrecorded(args, command, message, reason, clock)
    except CommandRecordUnreadable as unreadable:
        print(f"aura: {message}", file=sys.stderr)
        print(f"aura: {unreadable}", file=sys.stderr)
        return 2
    print(f"aura: {message}", file=sys.stderr)
    return 2


def _fail_unrecorded(
    args: argparse.Namespace,
    command: CommandEnvelope,
    message: str,
    reason: str,
    clock: Callable[[], datetime],
) -> int:
    try:
        last_event = _last_audit_event(
            args.audit_dir,
            command.command_id,
        )
    except (OSError, ValueError, AuditEventNotFound) as lookup_error:
        failed = command.with_status(
            CommandStateMachine().transition(
                command.status,
                CommandState.FAILED,
            )
        )
        store_error = _store_terminal_command(args, failed)
        print(f"aura: {message}", file=sys.stderr)
        print(f"aura: {store_error or lookup_error}", file=sys.stderr)
        return 2
    if (
        last_event is not None
        and last_event.event_type is AuditEventType.COMMAND_REFUSED
    ):
        # The chain already ends with the refusal, so store the refused
        # record and append nothing (task 1.3, I2).
        return _store_refused_command(args, command, message)
    return _fail_command(
        args,
        command,
        reason,
        previous_event_id=last_event.event_id if last_event else None,
        clock=clock,
        message=message,
    )


def _store_refused_command(
    args: argparse.Namespace,
    command: CommandEnvelope,
    message: str,
) -> int:
    state_machine = CommandStateMachine()
    refused = command
    # A refusal is reachable only once evidence collection has started.
    if not state_machine.can_transition(refused.status, CommandState.REFUSED):
        refused = refused.with_status(
            state_machine.transition(
                refused.status, CommandState.COLLECTING_EVIDENCE
            )
        )
    refused = refused.with_status(
        state_machine.transition(refused.status, CommandState.REFUSED)
    )
    store_error = _store_terminal_command(args, refused)
    print(f"aura: {message}", file=sys.stderr)
    if store_error is not None:
        print(f"aura: {store_error}", file=sys.stderr)
    return 2


def _last_audit_event(
    audit_dir: Path | None,
    command_id: UUID,
) -> AuditEvent | None:
    """Return the stored event of a command that no other event follows."""

    if audit_dir is None or not audit_dir.exists():
        return None
    store = JsonAuditEventStore(audit_dir)
    events: list[AuditEvent] = []
    for record_id in store.record_ids():
        try:
            event = store.load(record_id)
        except ValueError:
            # Another command's malformed record does not affect this
            # command's chain (task 1.2); one that may be this command's
            # still fails closed.
            if _names_another_command(store, record_id, command_id):
                continue
            raise
        if event.command_id == command_id:
            events.append(event)
    followed = {event.previous_event_id for event in events}
    return next(
        (event for event in events if event.event_id not in followed),
        None,
    )


def _complete_command(
    args: argparse.Namespace,
    command: CommandEnvelope,
    output: str,
) -> int:
    state_machine = CommandStateMachine()
    succeeded = (
        command
        if command.status is CommandState.SUCCEEDED
        else command.with_status(
            state_machine.transition(
                command.status,
                CommandState.SUCCEEDED,
            )
        )
    )
    command_store_error = _store_terminal_command(
        args,
        succeeded,
    )
    if command_store_error is not None:
        print(f"aura: {command_store_error}", file=sys.stderr)
        return 2
    print(output)
    return 0


def _names_another_command(
    store: JsonAuditEventStore,
    record_id: UUID,
    command_id: UUID,
) -> bool:
    """Return whether an unloadable audit record names another command."""

    try:
        document = from_json(
            store.root.joinpath(f"{record_id}.json").read_bytes()
        )
        other = document.get("command_id") if isinstance(document, dict) else None
        return isinstance(other, str) and UUID(other) != command_id
    except ValueError:
        return False


def _fail_command(
    args: argparse.Namespace,
    command: CommandEnvelope,
    error: str,
    *,
    previous_event_id: UUID | None,
    clock: Callable[[], datetime],
    message: str | None = None,
) -> int:
    """Store a failed command; `message`, when given, is printed instead
    of the stored `error`."""

    state_machine = CommandStateMachine()
    failed = command.with_status(
        state_machine.transition(command.status, CommandState.FAILED)
    )
    command_store_error = _store_terminal_command(args, failed)
    if args.audit_dir is not None:
        event = _command_terminal_audit_event(
            failed,
            AuditEventType.COMMAND_FAILED,
            error,
            previous_event_id=previous_event_id,
            occurred_at=clock(),
        )
        audit_error = _record_store_error(
            lambda: JsonAuditEventStore(args.audit_dir).save(event),
            (AuditEventAlreadyStored,),
        )
        command_store_error = command_store_error or audit_error
    print(f"aura: {command_store_error or message or error}", file=sys.stderr)
    return 2


def _make_command(
    args: argparse.Namespace,
    command_type: CommandType,
    command_input: dict[str, object],
    command_id_factory: Callable[[], UUID],
    clock: Callable[[], datetime],
) -> CommandEnvelope:
    return CommandEnvelope(
        command_id=command_id_factory(),
        command_type=command_type,
        operator_id=args.operator,
        created_at=clock(),
        input=command_input,
        idempotency_key=args.idempotency_key,
        status=CommandState.RECEIVED,
    )


def _run_evidence_command(
    action: Callable[[], EvidenceCommandResult],
    errors: tuple[type[Exception], ...],
) -> EvidenceCommandResult | str:
    try:
        return action()
    except errors as error:
        return str(error)


@dataclass(frozen=True)
class EvidenceCommandRoute:
    service_type: type[EvidenceCommandService]
    command_input: Callable[[argparse.Namespace], dict[str, object]]
    select_collector: Callable[
        [argparse.Namespace, EvidenceCollector | None],
        EvidenceCollector,
    ]
    source_errors: tuple[type[Exception], ...]
    assemble: bool = False

    @property
    def command_type(self) -> CommandType:
        return self.service_type.spec.command_type

    @property
    def errors(self) -> tuple[type[Exception], ...]:
        spec = self.service_type.spec
        return (
            spec.unauthorized_error,
            spec.invalid_error,
            InvalidCommandTransition,
            *self.source_errors,
        )


@dataclass(frozen=True)
class AssembledCommandResult:
    command: CommandEnvelope
    answer: DiagnosisAnswer | None
    refusal_error: str | None
    last_event: AuditEvent | None


@dataclass(frozen=True)
class AssemblyFailure:
    error: str
    last_event: AuditEvent | None


@dataclass(frozen=True)
class CollectedEvidence:
    result: EvidenceCommandResult
    audit_event: AuditEvent | None


def _run_simple_evidence_command(
    args: argparse.Namespace,
    route: EvidenceCommandRoute,
    collector: EvidenceCollector | None,
    command_id_factory: Callable[[], UUID],
    clock: Callable[[], datetime],
) -> int:
    command = _make_command(
        args,
        route.command_type,
        {"query": args.query, **route.command_input(args), "history": []},
        command_id_factory,
        clock,
    )
    return _execute_command(
        args,
        command,
        lambda: _run_simple_command(
            args,
            route,
            route.select_collector(args, collector),
            command,
            roles={OperatorRole(role) for role in args.role},
            assemble=route.assemble,
            clock=clock,
        ),
        clock,
    )


def _run_simple_command(
    args: argparse.Namespace,
    route: EvidenceCommandRoute,
    selected_collector: EvidenceCollector,
    command: CommandEnvelope,
    *,
    roles: set[OperatorRole],
    assemble: bool,
    clock: Callable[[], datetime],
) -> int:
    service = route.service_type(collector=selected_collector)
    collected = _collect_evidence(
        args,
        command,
        lambda: service.run(command, roles),
        route.errors,
        clock,
    )
    if isinstance(collected, int):
        return collected

    result = collected.result
    evidence_event = collected.audit_event

    final_command = result.command

    if assemble and args.cwa_dir is not None:
        assembled = _assemble_and_diagnose(
            args,
            result,
            roles,
            evidence_event,
            output_schema="inventory_list",
            diagnosis_model=None,
            always_diagnose=False,
            clock=clock,
        )
        if isinstance(assembled, AssemblyFailure):
            return _fail_assembly(args, result, assembled, clock)
        final_command = assembled.command

    return _complete_command(
        args,
        final_command,
        result.snapshot.model_dump_json(indent=2),
    )


def _collect_evidence(
    args: argparse.Namespace,
    command: CommandEnvelope,
    action: Callable[[], EvidenceCommandResult],
    errors: tuple[type[Exception], ...],
    clock: Callable[[], datetime],
) -> CollectedEvidence | int:
    received_event, received_error = _persist_command_received(
        args,
        command,
    )
    if received_error is not None:
        return _fail_command(
            args,
            command,
            received_error,
            previous_event_id=(
                received_event.event_id if received_event else None
            ),
            clock=clock,
        )

    result = _run_evidence_command(action, errors)
    if isinstance(result, str):
        return _fail_command(
            args,
            command,
            result,
            previous_event_id=(
                received_event.event_id if received_event else None
            ),
            clock=clock,
        )

    evidence_event, persistence_error = _persist_evidence(
        args,
        command,
        result,
        received_event.event_id if received_event else None,
    )
    if persistence_error is not None:
        return _fail_command(
            args,
            result.command,
            persistence_error,
            previous_event_id=(
                received_event.event_id if received_event else None
            ),
            clock=clock,
        )

    return CollectedEvidence(result=result, audit_event=evidence_event)


def _fail_assembly(
    args: argparse.Namespace,
    result: EvidenceCommandResult,
    failure: AssemblyFailure,
    clock: Callable[[], datetime],
) -> int:
    return _fail_command(
        args,
        result.command,
        failure.error,
        previous_event_id=(
            failure.last_event.event_id if failure.last_event else None
        ),
        clock=clock,
    )


def _save_audit_chain(
    audit_dir: Path,
    events: Sequence[AuditEvent],
) -> tuple[AuditEvent | None, str | None]:
    store = JsonAuditEventStore(audit_dir)
    stored: AuditEvent | None = None
    for event in events:
        error = _record_store_error(
            partial(store.save, event),
            (AuditEventAlreadyStored,),
        )
        if error is not None:
            return stored, error
        stored = event
    return stored, None


def _selected_collector(
    args: argparse.Namespace,
    collector: EvidenceCollector | None,
) -> EvidenceCollector:
    if collector is not None:
        return collector
    if args.source == "combined":
        return ClusterInventoryCollector(
            git_collector=GitInventoryCollector(args.fleet_clusters),
            openshift_collector=OpenShiftInventoryCollector(
                client=OpenShiftCLIInventoryClient(
                    kubeconfig_path=args.openshift_kubeconfig
                )
            ),
            tekton_collector=TektonInventoryCollector(
                client=OpenShiftCLITektonClient(
                    kubeconfig_path=args.openshift_kubeconfig,
                )
            ),
        )
    if args.source == "openshift":
        return OpenShiftInventoryCollector(
            client=OpenShiftCLIInventoryClient(
                kubeconfig_path=args.openshift_kubeconfig
            )
        )
    return GitInventoryCollector(args.fleet_clusters)


def _selected_workflow_collector(
    args: argparse.Namespace,
    collector: EvidenceCollector | None,
) -> EvidenceCollector:
    if collector is not None:
        return collector
    return WorkflowInventoryCollector(
        git_collector=(
            GitInventoryCollector(args.fleet_clusters)
            if args.fleet_clusters is not None
            else None
        ),
        openshift_collector=OpenShiftInventoryCollector(
            client=OpenShiftCLIInventoryClient(
                kubeconfig_path=args.openshift_kubeconfig,
            )
        ),
        tekton_collector=TektonInventoryCollector(
            client=OpenShiftCLITektonClient(
                kubeconfig_path=args.openshift_kubeconfig,
                namespace=args.namespace,
            )
        ),
    )


def _selected_cluster_state_collector(
    args: argparse.Namespace,
    collector: EvidenceCollector | None,
) -> EvidenceCollector:
    if collector is not None:
        return collector
    return ClusterStateCollector(
        git_collector=GitInventoryCollector(args.fleet_clusters),
        openshift_collector=OpenShiftInventoryCollector(
            client=OpenShiftCLIInventoryClient(
                kubeconfig_path=args.openshift_kubeconfig,
            )
        ),
        tekton_collector=TektonInventoryCollector(
            client=OpenShiftCLITektonClient(
                kubeconfig_path=args.openshift_kubeconfig,
            )
        ),
        cluster_name=args.cluster,
        pipeline_run=getattr(args, "pipeline_run", None),
        pipeline_name=getattr(args, "pipeline_name", None),
    )


def _selected_proposal_collector(
    args: argparse.Namespace,
    collector: EvidenceCollector | None,
) -> EvidenceCollector:
    if collector is None and args.proposal_type == _SCAFFOLD:
        return ScaffoldEvidenceCollector(
            request=_scaffold_request(args),
            git_collector=GitInventoryCollector(args.fleet_clusters),
            openshift_client=OpenShiftCLIInventoryClient(
                kubeconfig_path=args.openshift_kubeconfig,
            ),
            scaffold_checkout=args.scaffold,
            kustomize_version=kustomize_version,
        )
    if collector is not None or args.proposal_type != _RERUN:
        return _selected_cluster_state_collector(args, collector)
    return RerunEvidenceCollector(
        cluster_state=_selected_cluster_state_collector(args, None),
        pipeline_definitions=PipelineDefinitionCollector(
            client=OpenShiftCLIPipelineClient(
                kubeconfig_path=args.openshift_kubeconfig,
            ),
            pipeline_names={"post-provision"},
        ),
    )


def _selected_proposal_status_collector(
    args: argparse.Namespace,
    collector: EvidenceCollector | None,
) -> EvidenceCollector:
    return collector or ProposalStalenessCollector(
        proposal_store=JsonProposalStore(args.proposal_dir),
        staleness=GitProposalStaleness(args.fleet_clusters),
        proposal_ids=args.proposal_id,
    )


_EVIDENCE_ROUTES = {
    "inventory": EvidenceCommandRoute(
        service_type=InventoryService,
        command_input=lambda args: {},
        select_collector=_selected_collector,
        source_errors=(OpenShiftCLIError, GitInventoryError),
        assemble=True,
    ),
    "workflow": EvidenceCommandRoute(
        service_type=WorkflowService,
        command_input=lambda args: {"namespace": args.namespace},
        select_collector=_selected_workflow_collector,
        source_errors=(OpenShiftCLIError, GitInventoryError),
    ),
    "proposal": EvidenceCommandRoute(
        service_type=ProposalStatusService,
        command_input=lambda args: {
            "proposal_ids": [str(item) for item in args.proposal_id],
        },
        select_collector=_selected_proposal_status_collector,
        source_errors=(
            GitInventoryError,
            ProposalNotFound,
            ProposalRecordUnreadable,
        ),
    ),
}


def _diagnosis_record(
    command_id: UUID,
    snapshot: CWASnapshot,
    diagnosed_at: datetime,
    model: DiagnosisModel | None = None,
) -> DiagnosisRecord:
    result = DiagnosisService(model=model).diagnose(snapshot)
    return DiagnosisRecord(
        diagnosis_id=uuid5(
            command_id,
            f"diagnosis:{snapshot.snapshot_id}",
        ),
        command_id=command_id,
        cwa_snapshot_id=snapshot.snapshot_id,
        cwa_snapshot_digest=snapshot.snapshot_digest,
        provider_name=result.provider_name,
        model_name=result.model_name,
        used_fallback=result.used_fallback,
        diagnosed_at=diagnosed_at,
        response=result.response,
    )


def _result_or_refusal(
    action: Callable[[], T],
    refusal: type[Exception],
    label: str,
) -> tuple[T | None, str | None]:
    try:
        return action(), None
    except refusal as error:
        return None, f"{label} refused: {error}"


def _model_audit_event(
    command: CommandEnvelope,
    diagnosis_record: DiagnosisRecord,
    trace_id: UUID,
    previous_event_id: UUID,
    persisted: bool,
) -> AuditEvent:
    return AuditEvent(
        event_id=uuid5(
            command.command_id,
            f"model_called:{diagnosis_record.diagnosis_id}",
        ),
        command_id=command.command_id,
        event_type=AuditEventType.MODEL_CALLED,
        occurred_at=diagnosis_record.diagnosed_at,
        actor=command.operator_id,
        data={
            "diagnosis_id": str(diagnosis_record.diagnosis_id),
            "response_digest": diagnosis_record.response_digest,
            "cwa_snapshot_id": str(diagnosis_record.cwa_snapshot_id),
            "cwa_snapshot_digest": (
                diagnosis_record.cwa_snapshot_digest
            ),
            "trace_id": str(trace_id),
            "provider_name": diagnosis_record.provider_name,
            "model_name": diagnosis_record.model_name,
            "used_fallback": diagnosis_record.used_fallback,
            "persisted": persisted,
        },
        previous_event_id=previous_event_id,
    )


def _diagnosis_trace(
    assembly: CWAAssemblyResult,
    diagnosis_record: DiagnosisRecord | None,
) -> CWATrace:
    if diagnosis_record is None:
        return assembly.trace
    return assembly.trace.with_response(
        diagnosis_record.provider_name,
        diagnosis_record.model_name,
        diagnosis_record.response_digest,
    )


def _diagnosis_answer(
    diagnosis_record: DiagnosisRecord,
    assembly: CWAAssemblyResult,
    evidence_snapshot: EvidenceSnapshot,
) -> DiagnosisAnswer:
    return DiagnosisAnswer(
        diagnosis=diagnosis_record,
        evidence_snapshot_id=evidence_snapshot.snapshot_id,
        evidence_snapshot_digest=evidence_snapshot.snapshot_digest,
        cwa_trace_id=assembly.trace.trace_id,
    )


def _diagnose_and_trace(
    args: argparse.Namespace,
    command_id: UUID,
    assembly: CWAAssemblyResult,
    diagnosis_model: DiagnosisModel | None,
    always_diagnose: bool,
    clock: Callable[[], datetime],
) -> tuple[DiagnosisRecord | None, str | None, CWATrace]:
    if not always_diagnose and args.diagnosis_dir is None:
        return None, None, assembly.trace

    diagnosed_at = clock()
    diagnosis_record, refusal_error = _result_or_refusal(
        lambda: _diagnosis_record(
            command_id,
            assembly.snapshot,
            diagnosed_at,
            model=diagnosis_model,
        ),
        DiagnosisEvidenceUnavailable,
        "diagnosis",
    )
    trace = _diagnosis_trace(assembly, diagnosis_record)
    if args.diagnosis_dir is not None and diagnosis_record is not None:
        JsonDiagnosisStore(args.diagnosis_dir).save(diagnosis_record)
    return diagnosis_record, refusal_error, trace


def _command_received_audit_event(
    command: CommandEnvelope,
) -> AuditEvent:
    return _audit_event(
        command,
        event_id=uuid5(command.command_id, "command_received"),
        event_type=AuditEventType.COMMAND_RECEIVED,
        occurred_at=command.created_at,
        data={
            "command_type": command.command_type.value,
            "request_digest": command.request_digest,
        },
    )


def _command_terminal_audit_event(
    command: CommandEnvelope,
    event_type: AuditEventType,
    reason: str,
    *,
    previous_event_id: UUID | None,
    occurred_at: datetime,
) -> AuditEvent:
    return _audit_event(
        command,
        event_id=uuid5(command.command_id, event_type.value),
        event_type=event_type,
        occurred_at=occurred_at,
        data={
            "command_type": command.command_type.value,
            "request_digest": command.request_digest,
            "status": command.status.value,
            "reason": reason,
        },
        previous_event_id=previous_event_id,
    )


def _persist_command_received(
    args: argparse.Namespace,
    command: CommandEnvelope,
) -> tuple[AuditEvent | None, str | None]:
    if args.audit_dir is None:
        return None, None
    event = _command_received_audit_event(command)
    error = _record_store_error(
        lambda: JsonAuditEventStore(args.audit_dir).save(event),
        (AuditEventAlreadyStored,),
    )
    if error is not None:
        return None, error
    return event, None


def _chained_audit_event(
    command: CommandEnvelope,
    event_type: AuditEventType,
    subject_id: UUID,
    *,
    occurred_at: datetime,
    data: dict[str, object],
    previous_event_id: UUID,
) -> AuditEvent:
    return _audit_event(
        command,
        event_id=uuid5(command.command_id, f"{event_type.value}:{subject_id}"),
        event_type=event_type,
        occurred_at=occurred_at,
        data=data,
        previous_event_id=previous_event_id,
    )


def _context_audit_event(
    command: CommandEnvelope,
    assembly: CWAAssemblyResult,
    evidence_snapshot: EvidenceSnapshot,
    previous_event_id: UUID,
) -> AuditEvent:
    return _chained_audit_event(
        command,
        AuditEventType.CONTEXT_ASSEMBLED,
        assembly.snapshot.snapshot_id,
        occurred_at=assembly.snapshot.assembled_at,
        data={
            "cwa_snapshot_id": str(assembly.snapshot.snapshot_id),
            "cwa_snapshot_digest": (
                assembly.snapshot.snapshot_digest
            ),
            "trace_id": str(assembly.trace.trace_id),
            "evidence_snapshot_id": str(
                evidence_snapshot.snapshot_id
            ),
            "evidence_snapshot_digest": (
                evidence_snapshot.snapshot_digest
            ),
            "assembler_version": assembly.trace.assembler_version,
            "persisted": True,
        },
        previous_event_id=previous_event_id,
    )


def _audit_event(
    command: CommandEnvelope,
    *,
    event_id: UUID,
    event_type: AuditEventType,
    occurred_at: datetime,
    data: dict[str, object],
    previous_event_id: UUID | None = None,
) -> AuditEvent:
    return AuditEvent.model_validate(
        {
            "event_id": event_id,
            "command_id": command.command_id,
            "event_type": event_type,
            "occurred_at": occurred_at,
            "actor": command.operator_id,
            "data": data,
            "previous_event_id": previous_event_id,
        }
    )


def _persist_evidence(
    args: argparse.Namespace,
    command: CommandEnvelope,
    result: EvidenceCommandResult,
    previous_event_id: UUID | None,
) -> tuple[AuditEvent | None, str | None]:
    if args.snapshot_dir is not None:
        try:
            JsonEvidenceSnapshotStore(args.snapshot_dir).save(
                result.snapshot
            )
        except (EvidenceSnapshotAlreadyStored, OSError) as error:
            return None, str(error)

    evidence_audit_event: AuditEvent | None = None
    if args.audit_dir is not None:
        snapshot = result.snapshot
        evidence_audit_event = _audit_event(
            command,
            event_id=uuid5(
                command.command_id,
                f"evidence_collected:{snapshot.snapshot_id}",
            ),
            event_type=AuditEventType.EVIDENCE_COLLECTED,
            occurred_at=snapshot.collected_at,
            data={
                "snapshot_id": str(snapshot.snapshot_id),
                "snapshot_digest": snapshot.snapshot_digest,
                "collector": snapshot.collector,
                "source": snapshot.source,
                "source_version": snapshot.source_version,
                "persisted": args.snapshot_dir is not None,
            },
            previous_event_id=previous_event_id,
        )
        error = _record_store_error(
            lambda: JsonAuditEventStore(args.audit_dir).save(
                evidence_audit_event
            ),
            (AuditEventAlreadyStored,),
        )
        if error is not None:
            return None, error

    return evidence_audit_event, None


def _assembly_request(
    result: EvidenceCommandResult,
    roles: set[OperatorRole],
    evidence_audit_event: AuditEvent,
    output_schema: str,
    clock: Callable[[], datetime],
) -> CWAAssemblyRequest:
    return CWAAssemblyRequest(
        command=result.command,
        evidence_snapshot=result.snapshot,
        audit_event=evidence_audit_event,
        operator_roles=roles,
        policy=CWAAssemblyPolicy(
            instructions={"policy": "read-only"},
            capabilities={"adapters": []},
            output_contract={"schema": output_schema},
            knowledge={"documents": []},
        ),
        assembled_at=clock(),
    )


def _assembled_command(
    command: CommandEnvelope,
    diagnosis_record: DiagnosisRecord | None,
    refusal_error: str | None,
) -> CommandEnvelope:
    state_machine = CommandStateMachine()
    if diagnosis_record is not None:
        model_called = command.with_status(
            state_machine.transition(
                command.status,
                CommandState.MODEL_CALLED,
            )
        )
        return model_called.with_status(
            state_machine.transition(
                model_called.status,
                CommandState.SUCCEEDED,
            )
        )
    terminal_state = (
        CommandState.REFUSED
        if refusal_error is not None
        else CommandState.SUCCEEDED
    )
    return command.with_status(
        state_machine.transition(command.status, terminal_state)
    )


def _assemble_records(
    args: argparse.Namespace,
    assembly_request: CWAAssemblyRequest,
    respond: Callable[
        [CWAAssemblyResult],
        tuple[T | None, str | None, CWATrace],
    ],
) -> tuple[CWAAssemblyResult, T | None, str | None] | str:
    try:
        assembly = CWAAssembler().assemble(assembly_request)
        JsonCWASnapshotStore(args.cwa_dir / "snapshots").save(
            assembly.snapshot
        )
        response, refusal_error, trace = respond(assembly)
        JsonCWATraceStore(args.cwa_dir / "traces").save(trace)
    except (
        InvalidCWAAssembly,
        CWASnapshotAlreadyStored,
        CWATraceAlreadyStored,
        DiagnosisAlreadyStored,
        ProposalGenerationError,
        OSError,
    ) as error:
        return str(error)
    return assembly, response, refusal_error


def _persist_assembly_events(
    args: argparse.Namespace,
    result: EvidenceCommandResult,
    assembly: CWAAssemblyResult,
    diagnosis_record: DiagnosisRecord | None,
    refusal_error: str | None,
    evidence_event: AuditEvent,
    clock: Callable[[], datetime],
) -> tuple[AuditEvent, AuditEvent | None, AuditEvent | None] | AssemblyFailure:
    context_event = _context_audit_event(
        result.command,
        assembly,
        result.snapshot,
        evidence_event.event_id,
    )
    model_event = (
        _model_audit_event(
            result.command,
            diagnosis_record,
            assembly.trace.trace_id,
            context_event.event_id,
            args.diagnosis_dir is not None,
        )
        if diagnosis_record is not None
        else None
    )
    refusal_event = (
        _command_terminal_audit_event(
            result.command,
            AuditEventType.COMMAND_REFUSED,
            refusal_error,
            previous_event_id=context_event.event_id,
            occurred_at=clock(),
        )
        if refusal_error is not None
        else None
    )
    stored, error = _save_audit_chain(
        args.audit_dir,
        [
            event
            for event in (context_event, model_event, refusal_event)
            if event is not None
        ],
    )
    if error is not None:
        return AssemblyFailure(error, stored or evidence_event)
    return context_event, model_event, refusal_event


def _assemble_and_diagnose(
    args: argparse.Namespace,
    result: EvidenceCommandResult,
    roles: set[OperatorRole],
    evidence_audit_event: AuditEvent | None,
    *,
    output_schema: str,
    diagnosis_model: DiagnosisModel | None,
    always_diagnose: bool,
    clock: Callable[[], datetime],
) -> AssembledCommandResult | AssemblyFailure:
    if evidence_audit_event is None:
        return AssemblyFailure(
            "audit event is required for CWA assembly",
            None,
        )
    assembly_request = _assembly_request(
        result,
        roles,
        evidence_audit_event,
        output_schema,
        clock,
    )

    records = _assemble_records(
        args,
        assembly_request,
        lambda assembly: _diagnose_and_trace(
            args,
            result.command.command_id,
            assembly,
            diagnosis_model,
            always_diagnose,
            clock,
        ),
    )
    if isinstance(records, str):
        return AssemblyFailure(records, evidence_audit_event)
    assembly, diagnosis_record, refusal_error = records

    events = _persist_assembly_events(
        args,
        result,
        assembly,
        diagnosis_record,
        refusal_error,
        evidence_audit_event,
        clock,
    )
    if isinstance(events, AssemblyFailure):
        return events
    context_event, model_event, refusal_event = events

    diagnosis_answer = (
        _diagnosis_answer(
            diagnosis_record,
            assembly,
            result.snapshot,
        )
        if diagnosis_record is not None
        else None
    )
    final_command = _assembled_command(
        result.command,
        diagnosis_record,
        refusal_error,
    )

    return AssembledCommandResult(
        command=final_command,
        answer=diagnosis_answer,
        refusal_error=refusal_error,
        last_event=refusal_event or model_event or context_event,
    )


def _execute_new_command(
    args: argparse.Namespace,
    command_type: CommandType,
    command_input: dict[str, object],
    run: Callable[[CommandEnvelope], int],
    *,
    command_id_factory: Callable[[], UUID],
    clock: Callable[[], datetime],
) -> int:
    command = _make_command(
        args,
        command_type,
        command_input,
        command_id_factory,
        clock,
    )
    return _execute_command(args, command, lambda: run(command), clock)


def _run_diagnosis(
    args: argparse.Namespace,
    command: CommandEnvelope,
    collector: EvidenceCollector | None,
    clock: Callable[[], datetime],
) -> int:
    selected_collector = _selected_cluster_state_collector(args, collector)
    service = ClusterStateService(collector=selected_collector)
    roles = {OperatorRole(role) for role in args.role}
    collected = _collect_evidence(
        args,
        command,
        lambda: service.diagnose_state(command, roles),
        (
            UnauthorizedClusterStateCommand,
            InvalidClusterStateCommand,
            InvalidCommandTransition,
            OpenShiftCLIError,
            GitInventoryError,
        ),
        clock,
    )
    if isinstance(collected, int):
        return collected

    result = collected.result
    evidence_event = collected.audit_event

    if args.cwa_dir is not None:
        assembled = _assemble_and_diagnose(
            args,
            result,
            roles,
            evidence_event,
            output_schema="diagnosis_state",
            diagnosis_model=ClusterStateDiagnosisModel(),
            always_diagnose=True,
            clock=clock,
        )
        if isinstance(assembled, AssemblyFailure):
            return _fail_assembly(args, result, assembled, clock)
        if assembled.refusal_error:
            command_store_error = _store_terminal_command(
                args,
                assembled.command,
            )
            print(
                f"aura: {command_store_error or assembled.refusal_error}",
                file=sys.stderr,
            )
            return 2
        if assembled.answer is None:
            return _fail_assembly(
                args,
                result,
                AssemblyFailure(
                    "CWA assembly did not return a diagnosis",
                    assembled.last_event,
                ),
                clock,
            )
        return _complete_command(
            args,
            assembled.command,
            assembled.answer.model_dump_json(indent=2),
        )

    print(
        "aura: --cwa-dir is required for diagnose state",
        file=sys.stderr,
    )
    return 2


def _run_proposal(
    args: argparse.Namespace,
    command: CommandEnvelope,
    collector: EvidenceCollector | None,
    clock: Callable[[], datetime],
) -> int:
    service_type = (
        PipelineProposalService
        if args.proposal_type == _RERUN
        else ProposalService
    )
    service = service_type(
        collector=_selected_proposal_collector(args, collector)
    )
    roles = {OperatorRole(role) for role in args.role}
    collected = _collect_evidence(
        args,
        command,
        lambda: service.run(command, roles),
        (
            UnauthorizedProposalCommand,
            InvalidProposalCommand,
            InvalidCommandTransition,
            OpenShiftCLIError,
            GitInventoryError,
            PipelineDefinitionError,
            ScaffoldCheckoutError,
            ScaffoldRunError,
        ),
        clock,
    )
    if isinstance(collected, int):
        return collected

    result = collected.result
    proposed = _assemble_and_propose(
        args,
        result,
        roles,
        collected.audit_event,
        clock,
    )
    if isinstance(proposed, AssemblyFailure):
        return _fail_assembly(args, result, proposed, clock)
    final_command, output, refusal_error = proposed
    if output is None:
        command_store_error = _store_terminal_command(args, final_command)
        print(
            f"aura: {command_store_error or refusal_error}",
            file=sys.stderr,
        )
        return 2
    return _complete_command(args, final_command, output)


_AUDITED_PROPOSAL = frozenset(
    {
        "proposal_id",
        "proposal_digest",
        "proposal_type",
        "cluster_name",
        "base_commit",
        "cwa_snapshot_id",
        "cwa_trace_id",
        "generator",
        "generator_version",
    }
)


def _assemble_and_propose(
    args: argparse.Namespace,
    result: EvidenceCommandResult,
    roles: set[OperatorRole],
    evidence_event: AuditEvent | None,
    clock: Callable[[], datetime],
) -> tuple[CommandEnvelope, str | None, str | None] | AssemblyFailure:
    """Store a proposal's records, returning its command and output JSON.

    The terminal command and output are computed before the proposal is
    stored, so only the terminal record write follows the proposal (I3).
    """

    if evidence_event is None:
        return AssemblyFailure(
            "audit event is required for CWA assembly",
            None,
        )
    records = _assemble_records(
        args,
        _assembly_request(result, roles, evidence_event, "proposal", clock),
        lambda assembly: _propose_and_trace(
            assembly,
            _proposal_generator(args),
            clock,
        ),
    )
    if isinstance(records, str):
        return AssemblyFailure(records, evidence_event)
    assembly, proposal, refusal_error = records
    terminal_state = (
        CommandState.SUCCEEDED
        if proposal is not None
        else CommandState.REFUSED
    )
    final_command = result.command.with_status(
        CommandStateMachine().transition(
            result.command.status,
            terminal_state,
        )
    )
    output = (
        proposal.model_dump_json(indent=2) if proposal is not None else None
    )

    context_event = _context_audit_event(
        result.command,
        assembly,
        result.snapshot,
        evidence_event.event_id,
    )
    terminal_event = (
        _proposal_created_audit_event(
            result.command,
            proposal,
            context_event.event_id,
        )
        if proposal is not None
        else _command_terminal_audit_event(
            result.command,
            AuditEventType.COMMAND_REFUSED,
            str(refusal_error),
            previous_event_id=context_event.event_id,
            occurred_at=clock(),
        )
    )
    stored, error = _save_audit_chain(
        args.audit_dir,
        (context_event, terminal_event),
    )
    if error is not None:
        return AssemblyFailure(error, stored or evidence_event)
    if proposal is not None:
        error = _record_store_error(
            partial(JsonProposalStore(args.proposal_dir).save, proposal),
            (ProposalAlreadyStored,),
        )
        if error is not None:
            return AssemblyFailure(error, terminal_event)
    return final_command, output, refusal_error


def _propose_and_trace(
    assembly: CWAAssemblyResult,
    generator: ProposalGenerator,
    clock: Callable[[], datetime],
) -> tuple[Proposal | None, str | None, CWATrace]:
    created_at = clock()
    proposal, refusal_error = _result_or_refusal(
        lambda: _generated(generator, assembly, created_at),
        ProposalRefused,
        "proposal",
    )
    if proposal is None:
        return None, refusal_error, assembly.trace
    return proposal, None, assembly.trace.with_response(
        generator.provider_name,
        generator.generator,
        proposal.proposal_digest,
    )


def _generated(
    generator: ProposalGenerator,
    assembly: CWAAssemblyResult,
    created_at: datetime,
) -> Proposal:
    """Generate a proposal, reporting a contract violation concisely."""

    try:
        return generator.generate(
            assembly.snapshot,
            cwa_trace_id=assembly.trace.trace_id,
            created_at=created_at,
        )
    except ValidationError as error:
        raise ProposalGenerationError(
            f"generated proposal is invalid: {error.errors()[0]['msg']}"
        ) from error
    except ScaffoldRunError as error:
        raise ProposalGenerationError(str(error)) from error


def _proposal_created_audit_event(
    command: CommandEnvelope,
    proposal: Proposal,
    previous_event_id: UUID,
) -> AuditEvent:
    recorded = proposal.model_dump(mode="json", include=_AUDITED_PROPOSAL)
    return _chained_audit_event(
        command,
        AuditEventType.PROPOSAL_CREATED,
        proposal.proposal_id,
        occurred_at=proposal.created_at,
        data=recorded,
        previous_event_id=previous_event_id,
    )


def _proposal_command_input(args: argparse.Namespace) -> dict[str, object]:
    """Return the request a proposal command's idempotency key binds."""

    command_input: dict[str, object] = {
        "query": args.query,
        "proposal_type": args.proposal_type,
        "cluster_name": args.cluster,
    }
    if args.proposal_type == _RERUN:
        command_input["pipeline_run"] = args.pipeline_run
    if args.proposal_type == _SCAFFOLD:
        command_input["scaffold_request"] = _scaffold_request(args).model_dump(
            mode="json"
        )
    command_input["history"] = []
    return command_input


def _dispatch_command(
    args: argparse.Namespace,
    *,
    collector: EvidenceCollector | None,
    command_id_factory: Callable[[], UUID],
    clock: Callable[[], datetime],
) -> int:
    route = _EVIDENCE_ROUTES.get(args.command)
    if route is not None:
        return _run_simple_evidence_command(
            args,
            route,
            collector,
            command_id_factory,
            clock,
        )
    if args.command == "diagnose":
        return _execute_new_command(
            args,
            CommandType.DIAGNOSE_STATE,
            {
                "query": args.query,
                "cluster_name": args.cluster,
                "pipeline_run": args.pipeline_run,
                "history": [],
            },
            lambda command: _run_diagnosis(args, command, collector, clock),
            command_id_factory=command_id_factory,
            clock=clock,
        )
    return _execute_new_command(
        args,
        (
            CommandType.PROPOSE_PIPELINE
            if args.proposal_type == _RERUN
            else CommandType.PROPOSE_PATCH
        ),
        _proposal_command_input(args),
        lambda command: _run_proposal(args, command, collector, clock),
        command_id_factory=command_id_factory,
        clock=clock,
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    collector: EvidenceCollector | None = None,
    command_id_factory: Callable[[], UUID] = uuid4,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> int:
    args = build_parser().parse_args(argv)
    args.role = args.role or [OperatorRole.VIEWER.value]
    argument_error = _argument_error(args)
    if argument_error is not None:
        print(
            f"aura: {argument_error}",
            file=sys.stderr,
        )
        return 2
    return _dispatch_command(
        args,
        collector=collector,
        command_id_factory=command_id_factory,
        clock=clock,
    )


__all__ = ["build_parser", "main"]
