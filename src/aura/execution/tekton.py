"""The Tekton adapter: create one approved post-provision PipelineRun
(Phase 3 task 4.1).

The run is named `aura-rerun-<proposal-id>`, so a second create of the same
proposal fails with `AlreadyExists` and starts no second run. It is created
with `oc` through the `aura-executor` kubeconfig, which only this module and
the configuration read (I10). An error the server stated is a failed result;
an error whose effect is unknown, including `AlreadyExists`, raises, so the
execution stays `awaiting_operator` until `execution status` resolves it.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID

from aura.adapters import AdapterName, AdapterOutcomeUnknown, AdapterRequest, AdapterResult
from aura.config import AuraConfig, ConfigError
from aura.process import ProcessError, ProcessResult, run_process
from aura.proposals import (
    RERUN_NAMESPACE,
    RERUN_PIPELINE,
    RERUN_PROPOSAL_ANNOTATION,
)

NAME_PREFIX = "aura-rerun-"
EXECUTION_ANNOTATION = "aura.openshiftpartnerlabs.com/execution-id"
PREFLIGHT_TIMEOUT_SECONDS = 30
# Errors the server stated: the create was refused, so nothing was created.
_REFUSED = frozenset(
    {
        "BadRequest",
        "Conflict",
        "Forbidden",
        "Invalid",
        "MethodNotAllowed",
        "NotFound",
        "RequestEntityTooLarge",
        "Unauthorized",
        "UnsupportedMediaType",
    }
)
_SERVER_ERROR = re.compile(r"^Error from server \((\w+)\)", re.MULTILINE)
_INVALID = re.compile(r'^The \S+ "[^"]*" is invalid', re.MULTILINE)

Runner = Callable[[Sequence[str], bytes | None, float], ProcessResult]


class TektonOutcomeUnknown(AdapterOutcomeUnknown):
    """The create may have reached the server; `execution status` resolves
    it by the run's deterministic name."""


def run_name(proposal_id: UUID) -> str:
    return f"{NAME_PREFIX}{proposal_id}"


def _run(argv: Sequence[str], input: bytes | None, timeout: float) -> ProcessResult:
    return run_process(argv, env=None, timeout=timeout, input=input)


@dataclass
class TektonAdapter:
    executor_kubeconfig: Path
    timeout_seconds: int
    runner: Runner = field(default=_run, repr=False)
    name: AdapterName = AdapterName.TEKTON
    version: str = "0.1.0"
    target: str = f"kubernetes-api:{RERUN_NAMESPACE}/pipelineruns"

    @classmethod
    def from_config(cls, config: AuraConfig) -> "TektonAdapter":
        kubeconfig = config.execution.executor_kubeconfig
        if kubeconfig is None:
            raise ConfigError("[execution] executor_kubeconfig is required for the tekton adapter")
        return cls(kubeconfig, config.execution.adapter_timeout_seconds)

    def _oc(self, *args: str) -> list[str]:
        return ["oc", "--kubeconfig", str(self.executor_kubeconfig), *args]

    def preflight(self) -> str | None:
        """Whether the executor identity can create PipelineRuns (G12)."""

        try:
            result = self.runner(
                self._oc(
                    "auth", "can-i", "create", "pipelineruns.tekton.dev",
                    "-n", RERUN_NAMESPACE,
                ),
                None,
                PREFLIGHT_TIMEOUT_SECONDS,
            )
        except ProcessError as error:
            return f"oc auth can-i did not complete: {error}"
        if result.stdout_text().strip() != "yes":
            return f"the executor cannot create PipelineRuns in {RERUN_NAMESPACE}"
        return None

    def write(self, request: AdapterRequest) -> AdapterResult:
        try:
            manifest = pipeline_run(request)
        except ValueError as error:
            return self._failed(request, f"refused before create: {error}")
        try:
            result = self.runner(
                self._oc("create", "-n", RERUN_NAMESPACE, "-f", "-", "-o", "json"),
                json.dumps(manifest).encode("utf-8"),
                self.timeout_seconds,
            )
        except ProcessError as error:
            if error.timed_out:
                raise TektonOutcomeUnknown("oc create timed out") from error
            # oc never started, so nothing reached the server.
            return self._failed(request, "oc could not start")
        if result.returncode == 0:
            return self._created(request, result)
        reason = _refusal(result.stderr_text())
        if reason is None:
            raise TektonOutcomeUnknown("oc create failed with an unknown outcome")
        # Only the reason class is stored: the server's text may quote the
        # manifest's values (I6).
        return self._failed(request, f"the server refused the create: {reason}")

    def _created(self, request: AdapterRequest, result: ProcessResult) -> AdapterResult:
        try:
            metadata = json.loads(result.stdout_text())["metadata"]
            name, uid = metadata["name"], metadata["uid"]
        except (ValueError, KeyError, TypeError) as error:
            raise TektonOutcomeUnknown("oc create returned unreadable output") from error
        if name != run_name(request.proposal_id) or not isinstance(uid, str) or not uid:
            raise TektonOutcomeUnknown("oc create returned another PipelineRun")
        return AdapterResult(
            request_id=request.request_id,
            execution_id=request.execution_id,
            adapter=self.name,
            success=True,
            external_result={"pipeline_run": name, "uid": uid, "namespace": RERUN_NAMESPACE},
        )

    def _failed(self, request: AdapterRequest, error: str) -> AdapterResult:
        return AdapterResult(
            request_id=request.request_id,
            execution_id=request.execution_id,
            adapter=self.name,
            success=False,
            error=error,
        )


def _refusal(stderr: str) -> str | None:
    """The reason class of an error the server stated, else None."""

    if _INVALID.search(stderr):
        return "Invalid"
    found = _SERVER_ERROR.search(stderr)
    if found and found.group(1) in _REFUSED:
        return found.group(1)
    # AlreadyExists, server faults, and connection errors leave the effect unknown.
    return None


def pipeline_run(request: AdapterRequest) -> dict[str, Any]:
    """The approved PipelineRun, named for the proposal and annotated with
    the execution; refuses any run but a post-provision rerun."""

    proposal = request.input.get("proposal")
    manifest = proposal.get("pipeline_run") if isinstance(proposal, dict) else None
    if not isinstance(manifest, dict):
        raise ValueError("the request carries no PipelineRun")
    if manifest.get("kind") != "PipelineRun" or not str(manifest.get("apiVersion", "")).startswith("tekton.dev/"):
        raise ValueError("the manifest is not a Tekton PipelineRun")
    if manifest.get("spec", {}).get("pipelineRef", {}).get("name") != RERUN_PIPELINE:
        raise ValueError(f"only the {RERUN_PIPELINE} pipeline may be run")
    metadata = dict(manifest.get("metadata") or {})
    if metadata.get("namespace") != RERUN_NAMESPACE:
        raise ValueError(f"the PipelineRun must be in {RERUN_NAMESPACE}")
    metadata.pop("generateName", None)
    metadata["name"] = run_name(request.proposal_id)
    metadata["annotations"] = {
        **(metadata.get("annotations") or {}),
        RERUN_PROPOSAL_ANNOTATION: str(request.proposal_id),
        EXECUTION_ANNOTATION: str(request.execution_id),
    }
    return {**manifest, "metadata": metadata}


__all__ = [
    "EXECUTION_ANNOTATION",
    "TektonAdapter",
    "TektonOutcomeUnknown",
    "pipeline_run",
    "run_name",
]
