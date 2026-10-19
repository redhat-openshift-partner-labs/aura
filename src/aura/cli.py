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
from aura.collectors.git_inventory import GitInventoryCollector
from aura.collectors.openshift_cli_client import OpenShiftCLIInventoryClient
from aura.collectors.openshift_inventory import OpenShiftInventoryCollector
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
from aura.cwa import CWASnapshot
from aura.diagnosis import DiagnosisRecord
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
from aura.services.inventory import (
    EvidenceCollector,
    InventoryService,
    InvalidCommandType,
    UnauthorizedCommand,
)
from aura.state import InvalidCommandTransition


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aura")
    command_parser = parser.add_subparsers(dest="command", required=True)
    inventory_parser = command_parser.add_parser("inventory")
    inventory_command_parser = inventory_parser.add_subparsers(
        dest="inventory_command",
        required=True,
    )
    list_parser = inventory_command_parser.add_parser("list")
    list_parser.add_argument(
        "--source",
        choices=["git", "openshift"],
        default="git",
        help="Inventory source to query",
    )
    list_parser.add_argument(
        "--fleet-clusters",
        type=Path,
        required=False,
        help="Path to the fleet-clusters Git checkout",
    )
    list_parser.add_argument(
        "--openshift-kubeconfig",
        type=Path,
        required=False,
        help="Path to a read-only OpenShift kubeconfig",
    )
    list_parser.add_argument(
        "--snapshot-dir",
        type=Path,
        required=False,
        help="Directory in which to store the evidence snapshot",
    )
    list_parser.add_argument(
        "--audit-dir",
        type=Path,
        required=False,
        help="Directory in which to store audit events",
    )
    list_parser.add_argument(
        "--cwa-dir",
        type=Path,
        required=False,
        help="Directory in which to store CWA snapshots and traces",
    )
    list_parser.add_argument(
        "--diagnosis-dir",
        type=Path,
        required=False,
        help="Directory in which to store diagnosis responses",
    )
    list_parser.add_argument(
        "--query",
        default="list inventory",
        help="Operator query used for CWA assembly",
    )
    list_parser.add_argument("--operator", required=True)
    list_parser.add_argument("--idempotency-key", required=True)
    list_parser.add_argument(
        "--role",
        action="append",
        choices=[role.value for role in OperatorRole],
        default=["viewer"],
        help="Operator role; may be repeated",
    )
    return parser


def _argument_error(args: argparse.Namespace) -> str | None:
    if args.source == "git" and args.fleet_clusters is None:
        return "--fleet-clusters is required for --source git"
    if args.source == "openshift" and args.openshift_kubeconfig is None:
        return "--openshift-kubeconfig is required for --source openshift"
    if args.cwa_dir is not None and args.audit_dir is None:
        return "--audit-dir is required with --cwa-dir"
    if args.diagnosis_dir is not None and args.cwa_dir is None:
        return "--cwa-dir is required with --diagnosis-dir"
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


def _diagnosis_record(
    command_id: UUID,
    snapshot: CWASnapshot,
    diagnosed_at: datetime,
) -> DiagnosisRecord:
    result = DiagnosisService().diagnose(snapshot)
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


def _model_audit_event(
    command: CommandEnvelope,
    diagnosis_record: DiagnosisRecord,
    trace_id: UUID,
    previous_event_id: UUID,
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
            "persisted": True,
        },
        previous_event_id=previous_event_id,
    )


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

    if args.snapshot_dir is not None:
        try:
            JsonEvidenceSnapshotStore(args.snapshot_dir).save(
                result.snapshot
            )
        except EvidenceSnapshotAlreadyStored as error:
            print(f"aura: {error}", file=stderr)
            return 2

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
            print(f"aura: {error}", file=stderr)
            return 2

    if args.cwa_dir is not None:
        assert evidence_audit_event is not None
        diagnosis_record: DiagnosisRecord | None = None
        assembly_request = CWAAssemblyRequest(
            command=result.command,
            evidence_snapshot=result.snapshot,
            audit_event=evidence_audit_event,
            operator_roles=roles,
            policy=CWAAssemblyPolicy(
                instructions={"policy": "read-only"},
                capabilities={"adapters": []},
                output_contract={"schema": "inventory_list"},
                knowledge={"documents": []},
            ),
            assembled_at=clock(),
        )
        try:
            assembly = CWAAssembler().assemble(assembly_request)
            JsonCWASnapshotStore(args.cwa_dir / "snapshots").save(
                assembly.snapshot
            )
            trace = assembly.trace
            if args.diagnosis_dir is not None:
                diagnosis_record = _diagnosis_record(
                    command.command_id,
                    assembly.snapshot,
                    clock(),
                )
                trace = trace.model_copy(
                    update={
                        "model_provider": diagnosis_record.provider_name,
                        "model_name": diagnosis_record.model_name,
                        "response_digest": (
                            diagnosis_record.response_digest
                        ),
                    }
                )
                JsonDiagnosisStore(args.diagnosis_dir).save(
                    diagnosis_record
                )
            JsonCWATraceStore(args.cwa_dir / "traces").save(trace)
        except (
            InvalidCWAAssembly,
            CWASnapshotAlreadyStored,
            CWATraceAlreadyStored,
            DiagnosisAlreadyStored,
        ) as error:
            print(f"aura: {error}", file=stderr)
            return 2

        context_event = _context_audit_event(
            command,
            assembly,
            result.snapshot,
            evidence_audit_event.event_id,
        )
        try:
            JsonAuditEventStore(args.audit_dir).save(context_event)
        except AuditEventAlreadyStored as error:
            print(f"aura: {error}", file=stderr)
            return 2

        if diagnosis_record is not None:
            model_event = _model_audit_event(
                command,
                diagnosis_record,
                assembly.trace.trace_id,
                context_event.event_id,
            )
            try:
                JsonAuditEventStore(args.audit_dir).save(model_event)
            except AuditEventAlreadyStored as error:
                print(f"aura: {error}", file=stderr)
                return 2

    print(result.snapshot.model_dump_json(indent=2))
    return 0


__all__ = ["build_parser", "main"]
