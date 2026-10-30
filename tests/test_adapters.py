from __future__ import annotations

from uuid import UUID

import pytest
from pydantic import ValidationError

from aura.adapters import (
    AdapterName,
    AdapterRequest,
    AdapterResult,
)


REQUEST: dict[str, object] = {
    "request_id": "00000000-0000-0000-0000-000000000030",
    "command_id": "00000000-0000-0000-0000-000000000001",
    "execution_id": "00000000-0000-0000-0000-000000000031",
    "proposal_id": "00000000-0000-0000-0000-000000000040",
    "proposal_digest": "a" * 64,
    "action_digest": "b" * 64,
    "adapter": "tekton",
    "input": {"cluster_name": "example"},
}
RESULT: dict[str, object] = {
    "request_id": "00000000-0000-0000-0000-000000000030",
    "execution_id": "00000000-0000-0000-0000-000000000031",
    "adapter": "tekton",
    "success": True,
    "external_result": {"pipeline_run": "aura-rerun-x"},
}


def make_request(**overrides: object) -> AdapterRequest:
    return AdapterRequest.model_validate({**REQUEST, **overrides})


def result(**overrides: object) -> AdapterResult:
    return AdapterResult.model_validate({**RESULT, **overrides})


def test_the_adapters_in_scope() -> None:
    assert {name.value for name in AdapterName} == {
        "git_handoff",
        "git_pr",
        "tekton",
        "recording",
    }


def test_valid_adapter_request() -> None:
    request = make_request()

    assert request.adapter is AdapterName.TEKTON
    assert request.action_digest == "b" * 64


def test_adapter_request_round_trips_through_json() -> None:
    request = make_request()

    restored = AdapterRequest.model_validate_json(request.model_dump_json())

    assert restored == request


def test_the_recording_adapter_records_and_writes_nothing_else() -> None:
    from aura.execution.recording import RecordingAdapter

    adapter = RecordingAdapter()
    request = make_request(adapter="recording")

    written = adapter.write(request)

    assert adapter.requests == [request]
    assert written.success and written.external_result == {
        "recorded": str(request.request_id)
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("request_id", "not-a-uuid"),
        ("execution_id", "not-a-uuid"),
        ("proposal_id", "not-a-uuid"),
        ("proposal_digest", "short"),
        ("action_digest", "short"),
        ("adapter", "scaffold"),
        ("extra", 1),
    ],
)
def test_adapter_request_rejects_invalid_fields(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        make_request(**{field: value})


def test_successful_adapter_result_names_its_effect() -> None:
    assert result().error is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"error": "unexpected"},
        {"external_result": {}},
        {"success": False, "external_result": {}},
        {"success": False, "error": " "},
    ],
    ids=["success-with-error", "success-without-effect", "failure-without-error", "blank-error"],
)
def test_adapter_result_rejects_inconsistent_values(overrides) -> None:
    with pytest.raises(ValidationError):
        result(**overrides)


def test_failed_adapter_result_carries_its_error() -> None:
    failed = result(success=False, external_result={}, error="create refused")

    assert failed.error == "create refused"
