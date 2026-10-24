from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from aura.collectors.openshift_cli_client import OpenShiftCLIInventoryClient
from aura.collectors.openshift_cli_pipeline_client import (
    PIPELINE_NAMESPACE,
    OpenShiftCLIPipelineClient,
)


DEPLOY = Path(__file__).parent.parent / "deploy"
API_GROUPS = {
    "clusterdeployments": "hive.openshift.io",
    "managedclusters": "cluster.open-cluster-management.io",
    "pipelineruns": "tekton.dev",
    "taskruns": "tekton.dev",
    "pipelines": "tekton.dev",
}
SUBJECT = {
    "kind": "ServiceAccount",
    "name": "aura-readonly",
    "namespace": "aura",
}


def documents() -> list[dict[str, Any]]:
    kustomization = yaml.safe_load((DEPLOY / "kustomization.yaml").read_text())
    return [
        document
        for resource in kustomization["resources"]
        for document in yaml.safe_load_all((DEPLOY / resource).read_text())
        if document
    ]


def by_kind(kind: str) -> list[dict[str, Any]]:
    return [document for document in documents() if document["kind"] == kind]


def granted(role: dict[str, Any]) -> set[tuple[str, str]]:
    return {
        (group, resource)
        for rule in role["rules"]
        for group in rule["apiGroups"]
        for resource in rule["resources"]
    }


def test_kustomization_lists_every_manifest() -> None:
    kustomization = yaml.safe_load((DEPLOY / "kustomization.yaml").read_text())

    assert sorted(kustomization["resources"]) == sorted(
        path.name
        for path in DEPLOY.glob("*.yaml")
        if path.name != "kustomization.yaml"
    )
    assert "namespace" not in kustomization


def test_manifests_create_only_read_only_access_objects() -> None:
    assert sorted(document["kind"] for document in documents()) == [
        "ClusterRole",
        "ClusterRoleBinding",
        "Namespace",
        "Role",
        "RoleBinding",
        "ServiceAccount",
    ]
    [account] = by_kind("ServiceAccount")
    assert account["metadata"] == {
        "name": "aura-readonly",
        "namespace": "aura",
    }


def test_every_rule_grants_only_list() -> None:
    for role in [*by_kind("ClusterRole"), *by_kind("Role")]:
        for rule in role["rules"]:
            assert rule["verbs"] == ["list"], role["metadata"]["name"]


def test_cluster_role_grants_exactly_the_cluster_wide_reads() -> None:
    inventory = set(OpenShiftCLIInventoryClient.resource_specs)
    [role] = by_kind("ClusterRole")

    assert granted(role) == {
        (API_GROUPS[resource], resource)
        for resource in inventory | {"pipelineruns", "taskruns"}
    }


def test_pipeline_role_grants_pipelines_in_their_namespace_only() -> None:
    [role] = by_kind("Role")

    assert role["metadata"]["namespace"] == PIPELINE_NAMESPACE
    assert granted(role) == {
        (API_GROUPS[resource], resource)
        for resource in OpenShiftCLIPipelineClient.resource_specs
    }


def test_bindings_bind_the_read_only_account() -> None:
    [cluster_role] = by_kind("ClusterRole")
    [role] = by_kind("Role")
    [cluster_binding] = by_kind("ClusterRoleBinding")
    [binding] = by_kind("RoleBinding")

    assert cluster_binding["subjects"] == [SUBJECT]
    assert binding["subjects"] == [SUBJECT]
    assert cluster_binding["roleRef"] == {
        "apiGroup": "rbac.authorization.k8s.io",
        "kind": "ClusterRole",
        "name": cluster_role["metadata"]["name"],
    }
    assert binding["roleRef"] == {
        "apiGroup": "rbac.authorization.k8s.io",
        "kind": "Role",
        "name": role["metadata"]["name"],
    }
    assert binding["metadata"]["namespace"] == PIPELINE_NAMESPACE
