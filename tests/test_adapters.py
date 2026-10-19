from __future__ import annotations

from uuid import UUID

import pytest
from pydantic import ValidationError

from aura.adapters import (
    AdapterName,
    AdapterRequest,
    AdapterResult,
    ExecutionAdapter,
)


def make_request(**overrides: object) -> AdapterRequest:
    values: dict[str, object] = {
        "request_id": "00000000-0000-0000-0000-000000000030",
        "command_id": "00000000-0000-0000-0000-000000000001",
        "proposal_id": "00000000-0000-0000-0000-000000000040",
        "adapter": "scaffold",
        "input": {"cluster_name": "example"},
    }
    values.update(overrides)
    return AdapterRequest.model_validate(values)


def test_valid_adapter_request() -> None:
    request = make_request()

    assert request.request_id == UUID("00000000-0000-0000-0000-000000000030")
    assert request.adapter is AdapterName.SCAFFOLD
    assert request.input == {"cluster_name": "example"}


def test_adapter_request_round_trips_through_json() -> None:
    request = make_request()

    restored = AdapterRequest.model_validate_json(request.model_dump_json())

    assert restored == request


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("request_id", "not-a-uuid"),
        ("command_id", "not-a-uuid"),
        ("proposal_id", "not-a-uuid"),
        ("adapter", "unknown-adapter"),
    ],
)
def test_adapter_request_rejects_invalid_fields(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        make_request(**{field: value})


def test_successful_adapter_result_has_no_error() -> None:
    result = AdapterResult.model_validate(
        {
            "request_id": "00000000-0000-0000-0000-000000000030",
            "command_id": "00000000-0000-0000-0000-000000000001",
            "adapter": "scaffold",
            "success": True,
            "output": {"generated_files": []},
        }
    )

    assert result.success is True
    assert result.error is None


def test_failed_adapter_result_requires_error() -> None:
    values: dict[str, object] = {
        "request_id": "00000000-0000-0000-0000-000000000030",
        "command_id": "00000000-0000-0000-0000-000000000001",
        "adapter": "scaffold",
        "success": False,
        "output": {},
    }

    with pytest.raises(ValidationError):
        AdapterResult.model_validate(values)


def test_successful_adapter_result_rejects_error() -> None:
    values: dict[str, object] = {
        "request_id": "00000000-0000-0000-0000-000000000030",
        "command_id": "00000000-0000-0000-0000-000000000001",
        "adapter": "scaffold",
        "success": True,
        "output": {},
        "error": "unexpected error",
    }

    with pytest.raises(ValidationError):
        AdapterResult.model_validate(values)


def test_execution_adapter_protocol_accepts_async_adapter() -> None:
    class ScaffoldAdapter:
        @property
        def name(self) -> AdapterName:
            return AdapterName.SCAFFOLD

        async def execute(self, request: AdapterRequest) -> AdapterResult:
            return AdapterResult(
                request_id=request.request_id,
                command_id=request.command_id,
                adapter=request.adapter,
                success=True,
                output={},
            )

    adapter: ExecutionAdapter = ScaffoldAdapter()

    assert adapter.name is AdapterName.SCAFFOLD

