"""Read the Phase 3 policy the hub holds: the allowed-signers list and the
execution store's identity (D7, D13).

Both ConfigMaps are read in one `oc get` through `aura-readonly`, so only
hub administrators can change who may approve and execute, or which store
real adapters use.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

from aura.collectors.oc_cli import (
    CommandRunner,
    OpenShiftCLIError,
    _read_oc,
    _run_command,
)


POLICY_NAMESPACE = "aura"
SIGNERS_CONFIGMAP = "aura-allowed-signers"
STORE_CONFIGMAP = "aura-execution-store"
POLICY_CONFIGMAPS = (SIGNERS_CONFIGMAP, STORE_CONFIGMAP)


class HubPolicyError(OpenShiftCLIError):
    """Raised when the hub policy ConfigMaps are missing or invalid."""


@dataclass(frozen=True)
class HubPolicy:
    allowed_signers: str
    deployment_id: UUID
    # Absent when the hub prunes PipelineRuns by count, so only a manual
    # resolution can record that a run never happened.
    pipeline_run_retention: timedelta | None


def read_hub_policy(
    *,
    kubeconfig_path: Path,
    command_runner: CommandRunner | None = None,
) -> HubPolicy:
    response = _read_oc(
        command_runner or _run_command,
        [
            "oc", "--kubeconfig", str(kubeconfig_path), "get", "configmap",
            *POLICY_CONFIGMAPS, "-n", POLICY_NAMESPACE, "-o", "json",
        ],
        "configmaps",
    )
    data = _configmap_data(response["items"])
    signers = data[SIGNERS_CONFIGMAP].get("allowed_signers")
    if not isinstance(signers, str) or not signers.strip():
        raise HubPolicyError(f"{SIGNERS_CONFIGMAP} has no allowed_signers")
    store = data[STORE_CONFIGMAP]
    try:
        deployment_id = UUID(str(store.get("deployment_id")))
    except ValueError as error:
        raise HubPolicyError(
            f"{STORE_CONFIGMAP} deployment_id is not a UUID"
        ) from error
    return HubPolicy(
        allowed_signers=signers,
        deployment_id=deployment_id,
        pipeline_run_retention=_retention(store),
    )


def _configmap_data(items: list[Any]) -> dict[str, dict[str, Any]]:
    data: dict[str, dict[str, Any]] = {}
    for item in items:
        name = (item.get("metadata") or {}).get("name") if isinstance(item, dict) else None
        if name not in POLICY_CONFIGMAPS:
            continue
        values = item.get("data", {})
        if name in data or not isinstance(values, dict):
            raise HubPolicyError(f"{name} is repeated or has invalid data")
        data[name] = values
    for name in POLICY_CONFIGMAPS:
        if name not in data:
            raise HubPolicyError(f"ConfigMap {name} not found in {POLICY_NAMESPACE}")
    return data


def _retention(store: dict[str, Any]) -> timedelta | None:
    value = store.get("pipeline_run_retention_seconds")
    if value is None:
        return None
    # isdigit alone accepts digits int() rejects, such as superscripts.
    if not isinstance(value, str) or not (value.isascii() and value.isdigit()) or int(value) <= 0:
        raise HubPolicyError(
            f"{STORE_CONFIGMAP} pipeline_run_retention_seconds is not a "
            "positive integer"
        )
    return timedelta(seconds=int(value))


__all__ = [
    "POLICY_CONFIGMAPS",
    "POLICY_NAMESPACE",
    "HubPolicy",
    "HubPolicyError",
    "read_hub_policy",
]
