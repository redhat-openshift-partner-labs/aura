"""The Tekton adapter against a fake `oc` (Phase 3 task 4.1)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from uuid import UUID

import pytest

from aura.adapters import AdapterName, AdapterRequest
from aura.config import ConfigError, ExecutionSettings
from aura.execution.tekton import (
    EXECUTION_ANNOTATION,
    TektonAdapter,
    TektonOutcomeUnknown,
    pipeline_run,
    run_name,
)
from aura.process import ProcessError, ProcessResult
from aura.proposals import RERUN_PROPOSAL_ANNOTATION

KUBECONFIG = Path("/kube/aura-executor")
PROPOSAL_ID = UUID("12345678-1234-5678-1234-567812345678")
EXECUTION_ID = UUID(int=50)


def manifest(**changes) -> dict:
    document = {
        "apiVersion": "tekton.dev/v1",
        "kind": "PipelineRun",
        "metadata": {
            "generateName": "post-provision-example-",
            "namespace": "openshift-pipelines",
            "annotations": {RERUN_PROPOSAL_ANNOTATION: str(PROPOSAL_ID)},
        },
        "spec": {"pipelineRef": {"name": "post-provision"}, "params": [{"name": "cluster", "value": "example"}]},
    }
    document.update(changes)
    return document


def request(pipeline: dict | None = None) -> AdapterRequest:
    return AdapterRequest(
        request_id=UUID(int=60),
        command_id=UUID(int=61),
        execution_id=EXECUTION_ID,
        proposal_id=PROPOSAL_ID,
        proposal_digest="2" * 64,
        action_digest="3" * 64,
        adapter=AdapterName.TEKTON,
        input={"proposal": {"pipeline_run": manifest() if pipeline is None else pipeline}},
    )


class FakeOc:
    """Records each call and answers with a fixed result or error."""

    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "", raises=None) -> None:
        self.calls: list[tuple[list[str], bytes | None, float]] = []
        self.result = ProcessResult(returncode, stdout.encode(), stderr.encode())
        self.raises = raises

    def __call__(self, argv, input, timeout):
        self.calls.append((list(argv), input, timeout))
        if self.raises is not None:
            raise self.raises
        return self.result


def created(name: str = run_name(PROPOSAL_ID), uid: str = "uid-1") -> FakeOc:
    return FakeOc(stdout=json.dumps({"metadata": {"name": name, "uid": uid}}))


def adapter(oc: FakeOc) -> TektonAdapter:
    return TektonAdapter(KUBECONFIG, 300, runner=oc)


def test_a_create_names_the_run_for_the_proposal() -> None:
    oc = created()

    result = adapter(oc).write(request())

    assert result.success
    assert result.external_result == {
        "pipeline_run": run_name(PROPOSAL_ID), "uid": "uid-1", "namespace": "openshift-pipelines",
    }
    ((argv, sent, timeout),) = oc.calls
    assert argv == [
        "oc", "--kubeconfig", str(KUBECONFIG), "create", "-n", "openshift-pipelines",
        "-f", "-", "-o", "json",
    ]
    assert timeout == 300
    body = json.loads(sent)
    assert body["metadata"]["name"] == f"aura-rerun-{PROPOSAL_ID}"
    assert "generateName" not in body["metadata"]
    assert body["metadata"]["annotations"] == {
        RERUN_PROPOSAL_ANNOTATION: str(PROPOSAL_ID),
        EXECUTION_ANNOTATION: str(EXECUTION_ID),
    }
    assert body["spec"] == manifest()["spec"]


def test_the_longest_name_is_a_valid_label() -> None:
    name = run_name(UUID("ffffffff-ffff-ffff-ffff-ffffffffffff"))

    assert len(name) == 47
    assert re.fullmatch(r"[a-z0-9]([-a-z0-9]*[a-z0-9])?", name)
    assert len(name) <= 63


@pytest.mark.parametrize(
    "stderr",
    [
        'Error from server (AlreadyExists): pipelineruns.tekton.dev "aura-rerun-x" already exists',
        "Error from server (InternalError): an error on the server",
        "Error from server (ServiceUnavailable): the server is currently unable to handle the request",
        "Error from server (Timeout): the server was unable to return a response",
        "Error from server (TooManyRequests): slow down",
        "Unable to connect to the server: dial tcp 10.0.0.1:6443: i/o timeout",
        "",
    ],
    ids=["already-exists", "internal", "unavailable", "server-timeout", "throttled", "connection", "silent"],
)
def test_an_error_with_an_unknown_effect_raises(stderr) -> None:
    oc = FakeOc(returncode=1, stderr=stderr)

    with pytest.raises(TektonOutcomeUnknown):
        adapter(oc).write(request())
    assert len(oc.calls) == 1


@pytest.mark.parametrize(
    ("stderr", "reason"),
    [
        ('Error from server (Forbidden): pipelineruns.tekton.dev is forbidden: User "x" cannot create', "Forbidden"),
        ("Error from server (Unauthorized): the server has asked for the client to provide credentials", "Unauthorized"),
        ('Error from server (NotFound): namespaces "openshift-pipelines" not found', "NotFound"),
        ('Error from server (BadRequest): admission webhook "x" denied the request: token=abc', "BadRequest"),
        ("Error from server (Conflict): operation cannot be fulfilled", "Conflict"),
        ('The PipelineRun "aura-rerun-x" is invalid: spec.params[0].value: secret-value', "Invalid"),
    ],
)
def test_an_error_the_server_stated_is_a_failure_naming_only_its_reason(stderr, reason) -> None:
    oc = FakeOc(returncode=1, stderr=stderr)

    result = adapter(oc).write(request())

    assert not result.success
    assert result.error == f"the server refused the create: {reason}"
    assert len(oc.calls) == 1


def test_a_timeout_raises() -> None:
    oc = FakeOc(raises=ProcessError("oc timed out after 300 seconds", timed_out=True))

    with pytest.raises(TektonOutcomeUnknown, match="timed out"):
        adapter(oc).write(request())


def test_oc_that_cannot_start_is_a_failure() -> None:
    oc = FakeOc(raises=ProcessError("oc could not start", timed_out=False))

    result = adapter(oc).write(request())

    assert (result.success, result.error) == (False, "oc could not start")


@pytest.mark.parametrize(
    "stdout",
    ["not json", json.dumps({"metadata": {"name": "other", "uid": "u"}}), json.dumps({"metadata": {"name": run_name(PROPOSAL_ID)}})],
    ids=["unparseable", "another-run", "no-uid"],
)
def test_a_create_whose_output_does_not_name_the_run_raises(stdout) -> None:
    with pytest.raises(TektonOutcomeUnknown):
        adapter(FakeOc(stdout=stdout)).write(request())


@pytest.mark.parametrize(
    ("pipeline", "reason"),
    [
        ({**manifest(), "spec": {"pipelineRef": {"name": "provision"}}}, "only the post-provision"),
        ({**manifest(), "spec": {"pipelineRef": {"name": "pre-provision"}}}, "only the post-provision"),
        ({**manifest(), "spec": {"pipelineRef": {"name": "deprovision"}}}, "only the post-provision"),
        ({**manifest(), "kind": "TaskRun"}, "not a Tekton PipelineRun"),
        ({**manifest(), "apiVersion": "v1"}, "not a Tekton PipelineRun"),
        ({**manifest(), "metadata": {"namespace": "fleet"}}, "must be in openshift-pipelines"),
        (None, "no PipelineRun"),
    ],
    ids=["provision", "pre-provision", "deprovision", "kind", "api-version", "namespace", "missing"],
)
def test_any_run_but_a_post_provision_rerun_is_refused_before_oc(pipeline, reason) -> None:
    oc = created()

    result = adapter(oc).write(_without_run() if pipeline is None else request(pipeline))

    assert not result.success
    assert reason in result.error
    assert oc.calls == []


def _without_run() -> AdapterRequest:
    return request().model_copy(update={"input": {"proposal": {}}})


def test_pipeline_run_keeps_every_field_but_the_name() -> None:
    body = pipeline_run(request())

    assert {key: value for key, value in body.items() if key != "metadata"} == {
        key: value for key, value in manifest().items() if key != "metadata"
    }


@pytest.mark.parametrize(
    ("result", "problem"),
    [
        (FakeOc(stdout="yes\n"), None),
        (FakeOc(returncode=1, stdout="no\n"), "cannot create PipelineRuns"),
        (FakeOc(raises=ProcessError("oc timed out", timed_out=True)), "did not complete"),
    ],
    ids=["allowed", "denied", "timeout"],
)
def test_preflight_asks_whether_the_executor_can_create_runs(result, problem) -> None:
    found = adapter(result).preflight()

    assert found is None if problem is None else problem in found
    ((argv, _, _),) = result.calls
    assert argv == [
        "oc", "--kubeconfig", str(KUBECONFIG), "auth", "can-i", "create",
        "pipelineruns.tekton.dev", "-n", "openshift-pipelines",
    ]


def test_the_adapter_targets_the_kubernetes_api() -> None:
    target = adapter(created()).target

    assert "eventlistener" not in target.lower()
    assert target == "kubernetes-api:openshift-pipelines/pipelineruns"


def test_the_tekton_adapter_requires_the_executor_kubeconfig() -> None:
    with pytest.raises(ValueError, match="executor_kubeconfig"):
        ExecutionSettings(adapters=["tekton"])
    with pytest.raises(ValueError, match="absolute"):
        ExecutionSettings(adapters=["tekton"], executor_kubeconfig=Path("kube"))


def test_from_config_refuses_a_missing_kubeconfig() -> None:
    from aura.config import AuraConfig

    config = AuraConfig.model_validate(
        {"store": {"backend": "sqlite", "path": "/tmp/aura.db", "login": "a@example.com"}}
    )

    with pytest.raises(ConfigError, match="executor_kubeconfig"):
        TektonAdapter.from_config(config)
