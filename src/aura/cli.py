from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from sys import stderr
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
)
from aura.evidence_store import (
    EvidenceSnapshotAlreadyStored,
    JsonEvidenceSnapshotStore,
)
from aura.services.cwa import (
    CWAAssemblyPolicy,
    CWAAssemblyRequest,
    CWAAssembler,
    InvalidCWAAssembly,
)
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


def main(
    argv: Sequence[str] | None = None,
    *,
    collector: EvidenceCollector | None = None,
    command_id_factory: Callable[[], UUID] = uuid4,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> int:
    args = build_parser().parse_args(argv)
    if args.source == "git" and args.fleet_clusters is None:
        print("aura: --fleet-clusters is required for --source git", file=stderr)
        return 2
    if args.source == "openshift" and args.openshift_kubeconfig is None:
        print(
            "aura: --openshift-kubeconfig is required for --source openshift",
            file=stderr,
        )
        return 2
    if args.cwa_dir is not None and args.audit_dir is None:
        print(
            "aura: --audit-dir is required with --cwa-dir",
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
    if collector is not None:
        selected_collector = collector
    elif args.source == "openshift":
        selected_collector = OpenShiftInventoryCollector(
            client=OpenShiftCLIInventoryClient(
                kubeconfig_path=args.openshift_kubeconfig
            )
        )
    else:
        selected_collector = GitInventoryCollector(args.fleet_clusters)

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
        evidence_audit_event = AuditEvent(
            event_id=uuid5(
                command.command_id,
                f"evidence_collected:{result.snapshot.snapshot_id}",
            ),
            command_id=command.command_id,
            event_type=AuditEventType.EVIDENCE_COLLECTED,
            occurred_at=result.snapshot.collected_at,
            actor=command.operator_id,
            data={
                "snapshot_id": str(result.snapshot.snapshot_id),
                "snapshot_digest": result.snapshot.snapshot_digest,
                "collector": result.snapshot.collector,
                "source": result.snapshot.source,
                "source_version": result.snapshot.source_version,
                "persisted": args.snapshot_dir is not None,
            },
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
            JsonCWATraceStore(args.cwa_dir / "traces").save(
                assembly.trace
            )
        except (
            InvalidCWAAssembly,
            CWASnapshotAlreadyStored,
            CWATraceAlreadyStored,
        ) as error:
            print(f"aura: {error}", file=stderr)
            return 2

        context_event = AuditEvent(
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
                    result.snapshot.snapshot_id
                ),
                "evidence_snapshot_digest": (
                    result.snapshot.snapshot_digest
                ),
                "assembler_version": assembly.trace.assembler_version,
                "persisted": True,
            },
            previous_event_id=evidence_audit_event.event_id,
        )
        try:
            JsonAuditEventStore(args.audit_dir).save(context_event)
        except AuditEventAlreadyStored as error:
            print(f"aura: {error}", file=stderr)
            return 2

    print(result.snapshot.model_dump_json(indent=2))
    return 0


__all__ = ["build_parser", "main"]
