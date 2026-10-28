from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml


POSTGRESQL = Path(__file__).parent.parent / "deploy" / "postgresql"
NAMESPACE = "aura-db"
# The official image, pinned to an exact release and Debian variant.
IMAGE = re.compile(r"docker\.io/library/postgres:\d+\.\d+-[a-z]+")


def kustomization() -> dict[str, Any]:
    return yaml.safe_load((POSTGRESQL / "kustomization.yaml").read_text())


def documents() -> list[dict[str, Any]]:
    return [
        document
        for resource in kustomization()["resources"]
        for document in yaml.safe_load_all((POSTGRESQL / resource).read_text())
        if document
    ]


def one(kind: str) -> dict[str, Any]:
    (document,) = [d for d in documents() if d["kind"] == kind]
    return document


def container() -> dict[str, Any]:
    (postgresql,) = one("StatefulSet")["spec"]["template"]["spec"]["containers"]
    return postgresql


def test_kustomization_lists_every_manifest() -> None:
    assert sorted(kustomization()["resources"]) == sorted(
        path.name
        for path in POSTGRESQL.glob("*.yaml")
        if path.name != "kustomization.yaml"
    )


def test_manifests_create_no_access_or_secret_objects() -> None:
    # The superuser Secret is created by the administrator, never committed,
    # and the database grants no Kubernetes access to anyone.
    assert sorted(d["kind"] for d in documents()) == [
        "Namespace",
        "Service",
        "StatefulSet",
    ]


def test_every_object_is_in_the_database_namespace() -> None:
    assert one("Namespace")["metadata"]["name"] == NAMESPACE
    for document in documents():
        if document["kind"] != "Namespace":
            assert document["metadata"]["namespace"] == NAMESPACE


def test_image_is_the_pinned_official_postgres_image() -> None:
    assert IMAGE.fullmatch(container()["image"])


def test_data_volume_is_mounted_where_the_image_declares_it() -> None:
    statefulset = one("StatefulSet")
    (claim,) = statefulset["spec"]["volumeClaimTemplates"]
    mounts = {m["name"]: m["mountPath"] for m in container()["volumeMounts"]}

    assert mounts[claim["metadata"]["name"]] == "/var/lib/postgresql"
    assert claim["spec"]["resources"]["requests"]["storage"] == "10Gi"
    assert "PGDATA" not in {e["name"] for e in container()["env"]}


def test_password_is_read_from_the_mounted_secret() -> None:
    pod = one("StatefulSet")["spec"]["template"]["spec"]
    env = {e["name"]: e for e in container()["env"]}
    mounts = {m["name"]: m for m in container()["volumeMounts"]}
    (secret_volume,) = [v for v in pod["volumes"] if "secret" in v]

    assert "POSTGRES_PASSWORD" not in env
    mount = mounts[secret_volume["name"]]
    assert mount["readOnly"] is True
    assert env["POSTGRES_PASSWORD_FILE"]["value"] == (
        f"{mount['mountPath']}/password"
    )
    assert secret_volume["secret"]["secretName"] == "aura-postgresql"


def test_pod_runs_restricted() -> None:
    pod = one("StatefulSet")["spec"]["template"]["spec"]
    context = container()["securityContext"]

    assert pod["automountServiceAccountToken"] is False
    assert pod["securityContext"]["runAsNonRoot"] is True
    assert "runAsUser" not in pod["securityContext"]
    assert context["allowPrivilegeEscalation"] is False
    assert context["capabilities"]["drop"] == ["ALL"]


def test_service_selects_the_statefulset_pods() -> None:
    service = one("Service")
    statefulset = one("StatefulSet")

    assert statefulset["spec"]["serviceName"] == service["metadata"]["name"]
    assert service["spec"]["selector"] == (
        statefulset["spec"]["template"]["metadata"]["labels"]
    )
