from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from sys import stderr
from uuid import UUID, uuid4

from aura.authorization import OperatorRole
from aura.collectors.git_inventory import GitInventoryCollector
from aura.collectors.openshift_cli_client import OpenShiftCLIInventoryClient
from aura.collectors.openshift_inventory import OpenShiftInventoryCollector
from aura.contracts import CommandEnvelope, CommandState, CommandType
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

    command_id = command_id_factory()
    command = CommandEnvelope(
        command_id=command_id,
        command_type=CommandType.INVENTORY_LIST,
        operator_id=args.operator,
        created_at=clock(),
        input={},
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

    print(result.snapshot.model_dump_json(indent=2))
    return 0


__all__ = ["build_parser", "main"]
