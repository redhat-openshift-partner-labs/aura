"""The aura-executor manifests in deploy/executor/ (Phase 3 D10, task 4.1)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

EXECUTOR = Path(__file__).resolve().parents[1] / "deploy" / "executor"


def documents() -> list[dict[str, Any]]:
    kustomization = yaml.safe_load((EXECUTOR / "kustomization.yaml").read_text())
    return [yaml.safe_load((EXECUTOR / name).read_text()) for name in kustomization["resources"]]


def test_the_kustomization_lists_every_manifest() -> None:
    kustomization = yaml.safe_load((EXECUTOR / "kustomization.yaml").read_text())

    assert sorted(kustomization["resources"]) == sorted(
        path.name for path in EXECUTOR.glob("*.yaml") if path.name != "kustomization.yaml"
    )


def test_the_executor_is_one_account_one_role_and_one_binding() -> None:
    kinds = {document["kind"]: document for document in documents()}

    assert sorted(kinds) == ["Role", "RoleBinding", "ServiceAccount"]
    assert kinds["ServiceAccount"]["metadata"] == {"name": "aura-executor", "namespace": "aura"}


def test_the_role_creates_and_gets_pipelineruns_in_their_namespace_only() -> None:
    (role,) = [d for d in documents() if d["kind"] == "Role"]

    assert role["metadata"]["namespace"] == "openshift-pipelines"
    assert role["rules"] == [
        {"apiGroups": ["tekton.dev"], "resources": ["pipelineruns"], "verbs": ["create", "get"]}
    ]


def test_the_binding_grants_the_role_to_the_executor_only() -> None:
    (binding,) = [d for d in documents() if d["kind"] == "RoleBinding"]

    assert binding["metadata"]["namespace"] == "openshift-pipelines"
    assert binding["roleRef"] == {
        "apiGroup": "rbac.authorization.k8s.io", "kind": "Role", "name": "aura-executor",
    }
    assert binding["subjects"] == [
        {"kind": "ServiceAccount", "name": "aura-executor", "namespace": "aura"}
    ]


def test_the_read_only_kustomization_does_not_include_the_executor() -> None:
    read_only = yaml.safe_load((EXECUTOR.parent / "kustomization.yaml").read_text())

    assert not [name for name in read_only["resources"] if "executor" in name]
