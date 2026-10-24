from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from subprocess import CalledProcessError
from typing import Any
from uuid import UUID

import pytest

from aura.collectors.oc_cli import OpenShiftCLIError
from aura.collectors.openshift_cli_pipeline_client import (
    OpenShiftCLIPipelineClient,
)
from aura.collectors.pipeline_definitions import (
    PipelineDefinitionCollector,
    PipelineDefinitionError,
)


NO_DEFAULT = object()


def param(name: str, default: object = NO_DEFAULT, **fields: object) -> dict:
    entry: dict[str, object] = {"name": name, "type": "string", **fields}
    if default is not NO_DEFAULT:
        entry["default"] = default
    return entry


def pipeline(name: str, *params: object) -> dict[str, Any]:
    spec: dict[str, Any] = {"tasks": [{"name": "configure"}]}
    if params:
        spec["params"] = list(params)
    return {
        "metadata": {"name": name, "namespace": "openshift-pipelines"},
        "spec": spec,
    }


POST_PROVISION = pipeline(
    "post-provision",
    param("cluster-name"),
    param("tier"),
    param("keycloak-admin-secret", "keycloak-admin"),
    param("dns-zones"),
    param("htpasswd-provider-name", "htpasswd"),
    param("openshift-ai-version", "v3"),
)


class FakePipelineClient:
    def __init__(self, *items: dict[str, Any]) -> None:
        self._items = list(items)

    def list_pipelines(self) -> list[dict[str, Any]]:
        return self._items

    def resource_version(self) -> str:
        return "pipelines=55"


def collect(*items: dict[str, Any], names: set[str] | None = None):
    return PipelineDefinitionCollector(
        client=FakePipelineClient(*items),
        pipeline_names=names,
    ).collect(
        command_id=UUID(int=1),
        collected_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def test_collector_records_parameter_definitions() -> None:
    snapshot = collect(POST_PROVISION)

    assert (snapshot.collector, snapshot.source) == (
        "pipeline_definitions",
        "tekton",
    )
    assert snapshot.source_version == "pipelines=55"
    assert snapshot.redactions == []
    [item] = snapshot.items
    assert item.kind == "pipeline_definition"
    assert item.data == {
        "namespace": "openshift-pipelines",
        "pipeline_name": "post-provision",
        "params": [
            {"name": "cluster-name", "type": "string", "has_default": False},
            {"name": "tier", "type": "string", "has_default": False},
            {
                "name": "keycloak-admin-secret",
                "type": "string",
                "has_default": True,
            },
            {"name": "dns-zones", "type": "string", "has_default": False},
            {
                "name": "htpasswd-provider-name",
                "type": "string",
                "has_default": True,
            },
            {
                "name": "openshift-ai-version",
                "type": "string",
                "has_default": True,
            },
        ],
        "required_params": ["cluster-name", "tier", "dns-zones"],
        "malformed_params": 0,
    }


def test_collector_never_stores_default_values() -> None:
    snapshot = collect(
        pipeline(
            "post-provision",
            param("github-token", "ghp_example"),
            param("spoke-kubeconfig", "users:\n- user: {token: sha256~abc}"),
            param("git-repo-url", "https://user:pw@example.com/fleet.git"),
            param("config", {"password": "hunter2"}, type="object"),
        )
    )

    dumped = snapshot.model_dump_json()
    for secret in ("ghp_example", "sha256~abc", "user:pw", "hunter2"):
        assert secret not in dumped
    [item] = snapshot.items
    assert all("default" not in entry for entry in item.data["params"])
    assert all(entry["has_default"] for entry in item.data["params"])
    assert snapshot.redactions == []


def test_collector_treats_null_default_as_required() -> None:
    [item] = collect(
        pipeline("post-provision", param("cluster-name", None))
    ).items

    assert item.data["required_params"] == ["cluster-name"]
    assert item.data["params"][0]["has_default"] is False


def test_collector_counts_duplicate_and_unknown_type_params() -> None:
    [item] = collect(
        pipeline(
            "post-provision",
            param("tier"),
            param("tier", "base"),
            param("zones", type="arrray"),
            param("labels", type={"k": 1}),
            param("hosts", type="array"),
        )
    ).items

    assert item.data["malformed_params"] == 3
    assert [entry["name"] for entry in item.data["params"]] == [
        "tier",
        "hosts",
    ]
    assert item.data["required_params"] == ["tier", "hosts"]


def test_collector_counts_non_mapping_spec_as_malformed() -> None:
    broken = pipeline("post-provision")
    broken["spec"] = "x"

    [item] = collect(broken).items

    assert (item.data["params"], item.data["malformed_params"]) == ([], 1)


@pytest.mark.parametrize(
    ("items", "names", "message"),
    [
        (
            (POST_PROVISION,),
            {"post-provision", "missing"},
            "requested Pipelines not found: ['missing']",
        ),
        (
            ({"spec": {}},),
            None,
            "Pipeline without a name in openshift-pipelines",
        ),
        (
            ({"metadata": {"name": None}, "spec": {}},),
            None,
            "Pipeline without a name in openshift-pipelines",
        ),
    ],
)
def test_collector_fails_closed_on_missing_or_unnamed_pipelines(
    items,
    names,
    message,
) -> None:
    with pytest.raises(PipelineDefinitionError, match=re.escape(message)):
        collect(*items, names=names)


def test_collector_counts_malformed_parameters() -> None:
    snapshot = collect(
        pipeline(
            "post-provision",
            "not-a-mapping",
            {"type": "string"},
            {"name": "", "type": "string"},
            {"name": "tier"},
        )
    )

    [item] = snapshot.items
    assert item.data["malformed_params"] == 3
    assert item.data["params"] == [
        {"name": "tier", "type": "string", "has_default": False}
    ]


@pytest.mark.parametrize(
    ("items", "names", "expected"),
    [
        ((), None, []),
        ((pipeline("empty"),), None, [("empty", [])]),
        (
            (POST_PROVISION, pipeline("provision", param("cluster-name"))),
            {"post-provision"},
            [("post-provision", ["cluster-name", "tier", "dns-zones"])],
        ),
    ],
)
def test_collector_handles_edge_inputs(items, names, expected) -> None:
    snapshot = collect(*items, names=names)

    assert [
        (item.data["pipeline_name"], item.data["required_params"])
        for item in snapshot.items
    ] == expected


def test_client_lists_pipelines_in_openshift_pipelines(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def runner(args: list[str]) -> str:
        calls.append(args)
        return json.dumps(
            {"metadata": {"resourceVersion": "55"}, "items": [POST_PROVISION]}
        )

    client = OpenShiftCLIPipelineClient(
        kubeconfig_path=tmp_path / "kubeconfig",
        command_runner=runner,
    )

    assert client.list_pipelines() == [POST_PROVISION]
    assert client.resource_version() == "pipelines=55"
    assert calls == [
        [
            "oc",
            "--kubeconfig",
            str(tmp_path / "kubeconfig"),
            "get",
            "pipelines",
            "-n",
            "openshift-pipelines",
            "-o",
            "json",
        ]
    ]


def test_client_reports_missing_rbac_concisely(tmp_path: Path) -> None:
    def forbidden(args: list[str]) -> str:
        raise CalledProcessError(
            1,
            args,
            stderr=(
                'Error from server (Forbidden): pipelines.tekton.dev is '
                'forbidden: User "system:serviceaccount:aura:aura-readonly" '
                'cannot list resource "pipelines"\n'
            ),
        )

    client = OpenShiftCLIPipelineClient(
        kubeconfig_path=tmp_path / "kubeconfig",
        command_runner=forbidden,
    )

    with pytest.raises(OpenShiftCLIError) as error:
        client.list_pipelines()

    assert str(error.value).startswith(
        "oc command failed for pipelines (exit 1): Error from server "
        "(Forbidden)"
    )


def test_collector_counts_non_list_parameters_as_malformed() -> None:
    broken = pipeline("post-provision")
    broken["spec"]["params"] = "cluster-name"

    [item] = collect(broken).items

    assert (item.data["params"], item.data["malformed_params"]) == ([], 1)
