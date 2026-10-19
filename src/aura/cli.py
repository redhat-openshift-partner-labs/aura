from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4, uuid5

from aura.audit_store import (
    AuditEventAlreadyStored,
    JsonAuditEventStore,
)
from aura.authorization import OperatorRole
from aura.collectors.cluster_state import ClusterStateCollector
from aura.collectors.git_inventory import GitInventoryCollector
from aura.collectors.openshift_cli_client import OpenShiftCLIInventoryClient
from aura.collectors.openshift_cli_tekton_client import (
    OpenShiftCLITektonClient,
)
from aura.collectors.openshift_inventory import OpenShiftInventoryCollector
from aura.collectors.tekton_inventory import TektonInventoryCollector
from aura.cwa_store import (
    CWASnapshotAlreadyStored,
    CWATraceAlreadyStored,
    JsonCWASnapshotStore,
    JsonCWATraceStore,
)
from aura.contracts import (
    AuditEvent,
    AuditEventType,
    CommandEnvelope,
    CommandState,
    CommandType,
    EvidenceSnapshot,
)
from aura.cwa import CWASnapshot, CWATrace
from aura.diagnosis import (
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
from aura.services.cwa import (
    CWAAssemblyResult,
    CWAAssemblyPolicy,
    CWAAssemblyRequest,
    CWAAssembler,
    InvalidCWAAssembly,
)
from aura.services.diagnosis import DiagnosisService
from aura.services.evidence import EvidenceCommandResult
from aura.services.cluster_state import (
    ClusterStateDiagnosisModel,
    ClusterStateService,
    InvalidClusterStateCommand,
    UnauthorizedClusterStateCommand,
)
from aura.services.inventory import (
    EvidenceCollector,
    InventoryService,
    InvalidCommandType,
    UnauthorizedCommand,
)
from aura.services.workflow import (
    InvalidWorkflowCommand,
    UnauthorizedWorkflowCommand,
    WorkflowService,
)
from aura.state import InvalidCommandTransition


def _add_evidence_output_arguments(
    parser: argparse.ArgumentParser,
) -> None:
    parser.add_argument(
        "--snapshot-dir",
        type=Path,
        required=False,
        help="Directory in which to store the evidence snapshot",
    )
    parser.add_argument(
        "--audit-dir",
        type=Path,
        required=False,
        help="Directory in which to store audit events",
    )


def _add_operator_arguments(
    parser: argparse.ArgumentParser,
) -> None:
    parser.add_argument("--operator", required=True)
    parser.add_argument("--idempotency-key", required=True)
    parser.add_argument(
        "--role",
        action="append",
        choices=[role.value for role in OperatorRole],
        default=["viewer"],
        help="Operator role; may be repeated",
    )


def _add_inventory_list_arguments(
    parser: argparse.ArgumentParser,
) -> None:
    parser.add_argument(
        "--source",
        choices=["git", "openshift"],
        default="git",
        help="Inventory source to query",
    )
    parser.add_argument(
        "--fleet-clusters",
        type=Path,
        required=False,
        help="Path to the fleet-clusters Git checkout",
    )
    parser.add_argument(
        "--openshift-kubeconfig",
        type=Path,
        required=False,
        help="Path to a read-only OpenShift kubeconfig",
    )
    _add_evidence_output_arguments(parser)
    parser.add_argument(
        "--cwa-dir",
        type=Path,
        required=False,
        help="Directory in which to store CWA snapshots and traces",
    )
    parser.add_argument(
        "--diagnosis-dir",
        type=Path,
        required=False,
        help="Directory in which to store diagnosis responses",
    )
    parser.add_argument(
        "--query",
        default="list inventory",
        help="Operator query used for CWA assembly",
    )
    _add_operator_arguments(parser)


def _add_workflow_inspect_arguments(
    parser: argparse.ArgumentParser,
) -> None:
    parser.add_argument(
        "--openshift-kubeconfig",
        type=Path,
        required=True,
        help="Path to a read-only OpenShift kubeconfig",
    )
    parser.add_argument(
        "--namespace",
        required=False,
        help="Tekton namespace to inspect; omit for all namespaces",
    )
    _add_evidence_output_arguments(parser)
    parser.add_argument(
        "--query",
        default="inspect workflows",
        help="Operator query recorded with the command",
    )
    _add_operator_arguments(parser)


def _add_diagnose_state_arguments(
    parser: argparse.ArgumentParser,
) -> None:
    parser.add_argument(
        "--cluster",
        required=True,
        help="Cluster name to diagnose",
    )
    parser.add_argument(
        "--fleet-clusters",
        type=Path,
        required=True,
        help="Path to the fleet-clusters Git checkout",
    )
    parser.add_argument(
        "--openshift-kubeconfig",
        type=Path,
        required=True,
        help="Path to a read-only OpenShift kubeconfig",
    )
    _add_evidence_output_arguments(parser)
    parser.add_argument(
        "--cwa-dir",
        type=Path,
        required=False,
        help="Directory in which to store CWA snapshots and traces",
    )
    parser.add_argument(
        "--diagnosis-dir",
        type=Path,
        required=False,
        help="Directory in which to store diagnosis responses",
    )
    parser.add_argument(
        "--query",
        default="diagnose cluster state",
        help="Operator query used for CWA assembly",
    )
    _add_operator_arguments(parser)


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
    return parser


def _inventory_argument_error(args: argparse.Namespace) -> str | None:
    if args.source == "git" and args.fleet_clusters is None:
        return "--fleet-clusters is required for --source git"
    if args.source == "openshift" and args.openshift_kubeconfig is None:
        return "--openshift-kubeconfig is required for --source openshift"
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
    return None


def _argument_error(args: argparse.Namespace) -> str | None:
    if args.command == "inventory":
        return _inventory_argument_error(args)
    if args.command == "workflow":
        return _workflow_argument_error(args)
    if args.command == "diagnose":
        return _diagnose_state_argument_error(args)
    return None


def _selected_collector(
    args: argparse.Namespace,
    collector: EvidenceCollector | None,
) -> EvidenceCollector:
    if collector is not None:
        return collector
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
    return TektonInventoryCollector(
        client=OpenShiftCLITektonClient(
            kubeconfig_path=args.openshift_kubeconfig,
            namespace=args.namespace,
        )
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
    )


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


def _diagnosis_or_refusal(
    command_id: UUID,
    snapshot: CWASnapshot,
    diagnosed_at: datetime,
    model: DiagnosisModel | None,
) -> tuple[DiagnosisRecord | None, str | None]:
    try:
        return _diagnosis_record(
            command_id,
            snapshot,
            diagnosed_at,
            model=model,
        ), None
    except DiagnosisEvidenceUnavailable as error:
        return None, f"diagnosis refused: {error}"


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
    return assembly.trace.model_copy(
        update={
            "model_provider": diagnosis_record.provider_name,
            "model_name": diagnosis_record.model_name,
            "response_digest": diagnosis_record.response_digest,
        }
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

    diagnosis_record, refusal_error = _diagnosis_or_refusal(
        command_id,
        assembly.snapshot,
        clock(),
        diagnosis_model,
    )
    trace = _diagnosis_trace(assembly, diagnosis_record)
    if args.diagnosis_dir is not None and diagnosis_record is not None:
        JsonDiagnosisStore(args.diagnosis_dir).save(diagnosis_record)
    return diagnosis_record, refusal_error, trace


def _evidence_audit_event(
    command: CommandEnvelope,
    snapshot: EvidenceSnapshot,
    persisted: bool,
) -> AuditEvent:
    return AuditEvent(
        event_id=uuid5(
            command.command_id,
            f"evidence_collected:{snapshot.snapshot_id}",
        ),
        command_id=command.command_id,
        event_type=AuditEventType.EVIDENCE_COLLECTED,
        occurred_at=snapshot.collected_at,
        actor=command.operator_id,
        data={
            "snapshot_id": str(snapshot.snapshot_id),
            "snapshot_digest": snapshot.snapshot_digest,
            "collector": snapshot.collector,
            "source": snapshot.source,
            "source_version": snapshot.source_version,
            "persisted": persisted,
        },
    )


def _context_audit_event(
    command: CommandEnvelope,
    assembly: CWAAssemblyResult,
    evidence_snapshot: EvidenceSnapshot,
    previous_event_id: UUID,
) -> AuditEvent:
    return AuditEvent(
        event_id=uuid5(
            command.command_id,
            f"context_assembled:{assembly.snapshot.snapshot_id}",
        ),
        command_id=command.command_id,
        event_type=AuditEventType.CONTEXT_ASSEMBLED,
        occurred_at=assembly.snapshot.assembled_at,
        actor=command.operator_id,
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


def _persist_evidence(
    args: argparse.Namespace,
    command: CommandEnvelope,
    result: EvidenceCommandResult,
) -> tuple[AuditEvent | None, str | None]:
    if args.snapshot_dir is not None:
        try:
            JsonEvidenceSnapshotStore(args.snapshot_dir).save(
                result.snapshot
            )
        except EvidenceSnapshotAlreadyStored as error:
            return None, str(error)

    evidence_audit_event: AuditEvent | None = None
    if args.audit_dir is not None:
        evidence_audit_event = _evidence_audit_event(
            command,
            result.snapshot,
            args.snapshot_dir is not None,
        )
        try:
            JsonAuditEventStore(args.audit_dir).save(
                evidence_audit_event
            )
        except AuditEventAlreadyStored as error:
            return None, str(error)

    return evidence_audit_event, None


def _assemble_and_diagnose(
    args: argparse.Namespace,
    command: CommandEnvelope,
    result: EvidenceCommandResult,
    roles: set[OperatorRole],
    *,
    output_schema: str,
    diagnosis_model: DiagnosisModel | None,
    always_diagnose: bool,
    clock: Callable[[], datetime],
) -> tuple[DiagnosisRecord | None, str | None]:
    evidence_audit_event = _evidence_audit_event(
        command,
        result.snapshot,
        args.snapshot_dir is not None,
    )
    assembly_request = CWAAssemblyRequest(
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

    try:
        assembly = CWAAssembler().assemble(assembly_request)
        JsonCWASnapshotStore(args.cwa_dir / "snapshots").save(
            assembly.snapshot
        )
        diagnosis_record, refusal_error, trace = _diagnose_and_trace(
            args,
            command.command_id,
            assembly,
            diagnosis_model,
            always_diagnose,
            clock,
        )
        JsonCWATraceStore(args.cwa_dir / "traces").save(trace)
    except (
        InvalidCWAAssembly,
        CWASnapshotAlreadyStored,
        CWATraceAlreadyStored,
        DiagnosisAlreadyStored,
    ) as error:
        return None, str(error)

    context_event = _context_audit_event(
        command,
        assembly,
        result.snapshot,
        evidence_audit_event.event_id,
    )
    try:
        JsonAuditEventStore(args.audit_dir).save(context_event)
    except AuditEventAlreadyStored as error:
        return None, str(error)

    if diagnosis_record is not None:
        model_event = _model_audit_event(
            command,
            diagnosis_record,
            assembly.trace.trace_id,
            context_event.event_id,
            args.diagnosis_dir is not None,
        )
        try:
            JsonAuditEventStore(args.audit_dir).save(model_event)
        except AuditEventAlreadyStored as error:
            return None, str(error)

    return diagnosis_record, refusal_error


def _run_workflow_inspect(
    args: argparse.Namespace,
    *,
    collector: EvidenceCollector | None,
    command_id_factory: Callable[[], UUID],
    clock: Callable[[], datetime],
) -> int:
    stderr = sys.stderr
    command = CommandEnvelope(
        command_id=command_id_factory(),
        command_type=CommandType.WORKFLOW_INSPECT,
        operator_id=args.operator,
        created_at=clock(),
        input={
            "query": args.query,
            "namespace": args.namespace,
            "history": [],
        },
        idempotency_key=args.idempotency_key,
        status=CommandState.RECEIVED,
    )
    selected_collector = _selected_workflow_collector(args, collector)
    service = WorkflowService(collector=selected_collector)
    roles = {OperatorRole(role) for role in args.role}

    try:
        result = service.inspect_workflows(command, roles)
    except (
        UnauthorizedWorkflowCommand,
        InvalidWorkflowCommand,
        InvalidCommandTransition,
    ) as error:
        print(f"aura: {error}", file=stderr)
        return 2

    _, persistence_error = _persist_evidence(args, command, result)
    if persistence_error is not None:
        print(f"aura: {persistence_error}", file=stderr)
        return 2

    print(result.snapshot.model_dump_json(indent=2))
    return 0


def _run_diagnose_state(
    args: argparse.Namespace,
    *,
    collector: EvidenceCollector | None,
    command_id_factory: Callable[[], UUID],
    clock: Callable[[], datetime],
) -> int:
    stderr = sys.stderr
    command = CommandEnvelope(
        command_id=command_id_factory(),
        command_type=CommandType.DIAGNOSE_STATE,
        operator_id=args.operator,
        created_at=clock(),
        input={
            "query": args.query,
            "cluster_name": args.cluster,
            "history": [],
        },
        idempotency_key=args.idempotency_key,
        status=CommandState.RECEIVED,
    )
    selected_collector = _selected_cluster_state_collector(args, collector)
    service = ClusterStateService(collector=selected_collector)
    roles = {OperatorRole(role) for role in args.role}

    try:
        result = service.diagnose_state(command, roles)
    except (
        UnauthorizedClusterStateCommand,
        InvalidClusterStateCommand,
        InvalidCommandTransition,
    ) as error:
        print(f"aura: {error}", file=stderr)
        return 2

    diagnosis_record: DiagnosisRecord | None = None
    _, persistence_error = _persist_evidence(args, command, result)
    if persistence_error is not None:
        print(f"aura: {persistence_error}", file=stderr)
        return 2

    if args.cwa_dir is not None:
        diagnosis_record, assembly_error = _assemble_and_diagnose(
            args,
            command,
            result,
            roles,
            output_schema="diagnosis_state",
            diagnosis_model=ClusterStateDiagnosisModel(),
            always_diagnose=True,
            clock=clock,
        )
        if assembly_error is not None:
            print(f"aura: {assembly_error}", file=stderr)
            return 2

    if diagnosis_record is None:
        print(
            "aura: --cwa-dir is required for diagnose state",
            file=stderr,
        )
        return 2

    print(diagnosis_record.response.model_dump_json(indent=2))
    return 0


def main(
    argv: Sequence[str] | None = None,
    *,
    collector: EvidenceCollector | None = None,
    command_id_factory: Callable[[], UUID] = uuid4,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> int:
    args = build_parser().parse_args(argv)
    stderr = sys.stderr
    argument_error = _argument_error(args)
    if argument_error is not None:
        print(
            f"aura: {argument_error}",
            file=stderr,
        )
        return 2

    if args.command == "workflow":
        return _run_workflow_inspect(
            args,
            collector=collector,
            command_id_factory=command_id_factory,
            clock=clock,
        )

    if args.command == "diagnose":
        return _run_diagnose_state(
            args,
            collector=collector,
            command_id_factory=command_id_factory,
            clock=clock,
        )

    command_id = command_id_factory()
    command = CommandEnvelope(
        command_id=command_id,
        command_type=CommandType.INVENTORY_LIST,
        operator_id=args.operator,
        created_at=clock(),
        input={"query": args.query, "history": []},
        idempotency_key=args.idempotency_key,
        status=CommandState.RECEIVED,
    )
    selected_collector = _selected_collector(args, collector)

    service = InventoryService(collector=selected_collector)
    roles = {OperatorRole(role) for role in args.role}

    try:
        result = service.list_inventory(command, roles)
    except (
        UnauthorizedCommand,
        InvalidCommandType,
        InvalidCommandTransition,
    ) as error:
        print(f"aura: {error}", file=stderr)
        return 2

    _, persistence_error = _persist_evidence(args, command, result)
    if persistence_error is not None:
        print(f"aura: {persistence_error}", file=stderr)
        return 2

    if args.cwa_dir is not None:
        _, assembly_error = _assemble_and_diagnose(
            args,
            command,
            result,
            roles,
            output_schema="inventory_list",
            diagnosis_model=None,
            always_diagnose=False,
            clock=clock,
        )
        if assembly_error is not None:
            print(f"aura: {assembly_error}", file=stderr)
            return 2

    print(result.snapshot.model_dump_json(indent=2))
    return 0


__all__ = ["build_parser", "main"]
