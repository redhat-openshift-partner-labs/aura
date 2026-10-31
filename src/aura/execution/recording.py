"""The test-only `recording` adapter: it records each request and writes
nothing outside Aura, so `execute` can be exercised end to end."""

from __future__ import annotations

from dataclasses import dataclass, field

from aura.adapters import AdapterName, AdapterRequest, AdapterResult


@dataclass
class RecordingAdapter:
    requests: list[AdapterRequest] = field(default_factory=list)
    name: AdapterName = AdapterName.RECORDING
    version: str = "0.1.0"
    target: str = "none"

    def preflight(self) -> str | None:
        return None

    def write(self, request: AdapterRequest) -> AdapterResult:
        self.requests.append(request)
        return AdapterResult(
            request_id=request.request_id,
            execution_id=request.execution_id,
            adapter=self.name,
            success=True,
            external_result={"recorded": str(request.request_id)},
        )


__all__ = ["RecordingAdapter"]
