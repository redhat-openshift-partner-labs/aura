"""The hub-held signer list and execution store ID (Phase 3 task 2.3)."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from aura.collectors.hub_policy import (
    POLICY_CONFIGMAPS,
    HubPolicy,
    HubPolicyError,
    read_hub_policy,
)
from aura.collectors.oc_cli import oc_call_budget


DEPLOYMENT = "6f1e2d3c-4b5a-4987-8a6b-5c4d3e2f1a0b"
SIGNERS = 'approver@example.com namespaces="aura-approve" ssh-ed25519 AAAA\n'


def configmap(name: str, data: dict[str, str]) -> dict[str, Any]:
    return {"kind": "ConfigMap", "metadata": {"name": name, "namespace": "aura"}, "data": data}


def response(*items: dict[str, Any]) -> str:
    return json.dumps({"kind": "List", "metadata": {"resourceVersion": ""}, "items": list(items)})


SIGNERS_MAP = configmap("aura-allowed-signers", {"allowed_signers": SIGNERS})
STORE_MAP = configmap(
    "aura-execution-store",
    {"deployment_id": DEPLOYMENT, "pipeline_run_retention_seconds": "86400"},
)


class Runner:
    def __init__(self, output: str) -> None:
        self.output = output
        self.calls: list[list[str]] = []

    def __call__(self, args: Sequence[str]) -> str:
        self.calls.append(list(args))
        return self.output


def read(output: str) -> tuple[HubPolicy, Runner]:
    runner = Runner(output)
    return read_hub_policy(kubeconfig_path=Path("/kube"), command_runner=runner), runner


def test_the_policy_is_read_in_one_oc_call() -> None:
    with oc_call_budget(limit=1):
        policy, runner = read(response(SIGNERS_MAP, STORE_MAP))

    assert policy == HubPolicy(
        allowed_signers=SIGNERS,
        deployment_id=UUID(DEPLOYMENT),
        pipeline_run_retention=timedelta(days=1),
    )
    assert runner.calls == [[
        "oc", "--kubeconfig", "/kube", "get", "configmap",
        *POLICY_CONFIGMAPS, "-n", "aura", "-o", "json",
    ]]


def test_retention_is_optional() -> None:
    store = configmap("aura-execution-store", {"deployment_id": DEPLOYMENT})

    policy, _ = read(response(SIGNERS_MAP, store))

    assert policy.pipeline_run_retention is None


@pytest.mark.parametrize(
    ("items", "message"),
    [
        ([SIGNERS_MAP], "aura-execution-store"),
        ([STORE_MAP], "aura-allowed-signers"),
        ([configmap("aura-allowed-signers", {"allowed_signers": " "}), STORE_MAP],
         "allowed_signers"),
        ([configmap("aura-allowed-signers", {}), STORE_MAP], "allowed_signers"),
        ([SIGNERS_MAP, configmap("aura-execution-store", {"deployment_id": "x"})],
         "deployment_id"),
        ([SIGNERS_MAP, configmap("aura-execution-store", {
            "deployment_id": DEPLOYMENT, "pipeline_run_retention_seconds": "0"})],
         "pipeline_run_retention_seconds"),
        ([SIGNERS_MAP, configmap("aura-execution-store", {
            "deployment_id": DEPLOYMENT, "pipeline_run_retention_seconds": "1d"})],
         "pipeline_run_retention_seconds"),
        ([SIGNERS_MAP, STORE_MAP, SIGNERS_MAP], "aura-allowed-signers"),
        ([{"kind": "ConfigMap", "metadata": {"name": "aura-allowed-signers"},
           "data": "text"}, STORE_MAP], "aura-allowed-signers"),
    ],
    ids=[
        "no-store-map",
        "no-signers-map",
        "blank-signers",
        "missing-signers",
        "bad-deployment-id",
        "zero-retention",
        "non-integer-retention",
        "repeated-map",
        "data-not-a-map",
    ],
)
def test_an_invalid_policy_is_an_error(items, message) -> None:
    with pytest.raises(HubPolicyError, match=message):
        read(response(*items))
