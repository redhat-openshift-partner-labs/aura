from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import yaml

import aura.cli  # registers every oc client subclass the CLI can build
from aura.collectors.oc_cli import OpenShiftCLIResourceClient
from aura.collectors.openshift_cli_client import OpenShiftCLIInventoryClient
from aura.collectors.openshift_cli_pipeline_client import (
    PIPELINE_NAMESPACE,
    OpenShiftCLIPipelineClient,
)
from aura.collectors.openshift_cli_tekton_client import (
    OpenShiftCLITektonClient,
)


DEPLOY = Path(__file__).parent.parent / "deploy"
CLIENTS = (
    OpenShiftCLIInventoryClient,
    OpenShiftCLITektonClient,
    OpenShiftCLIPipelineClient,
)
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


def refuse_oc(args: Sequence[str]) -> str:
    raise AssertionError(f"manifest tests must not run oc: {args}")


def client_reads() -> set[tuple[str | None, str]]:
    """(namespace, resource) for every read, from built clients.

    Clients are built rather than read from the class, because the Tekton
    client sets its specs per instance; its default reads all namespaces.
    """

    return {
        (options.get("namespace"), resource)
        for client in CLIENTS
        for resource, options in client(
            kubeconfig_path=Path("unused"),
            command_runner=refuse_oc,
        ).resource_specs.items()
    }


def concrete_subclasses(cls: type) -> set[type]:
    found = set()
    for subclass in cls.__subclasses__():
        found |= {subclass} | concrete_subclasses(subclass)
    return found


def test_every_oc_client_is_checked_against_the_manifests() -> None:
    assert concrete_subclasses(OpenShiftCLIResourceClient) == set(CLIENTS), (
        "a new oc client needs a list-only grant in deploy/ and an entry "
        "in CLIENTS"
    )


def test_cluster_role_grants_exactly_the_cluster_wide_reads() -> None:
    [role] = by_kind("ClusterRole")

    assert granted(role) == {
        (API_GROUPS[resource], resource)
        for namespace, resource in client_reads()
        if namespace is None
    }


def test_pipeline_role_grants_pipelines_in_their_namespace_only() -> None:
    [role] = by_kind("Role")
    namespaced = {
        (namespace, resource)
        for namespace, resource in client_reads()
        if namespace is not None
    }

    assert role["metadata"]["namespace"] == PIPELINE_NAMESPACE
    assert {namespace for namespace, _ in namespaced} == {PIPELINE_NAMESPACE}
    assert granted(role) == {
        (API_GROUPS[resource], resource) for _, resource in namespaced
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
