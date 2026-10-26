from __future__ import annotations

import json
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any, final

from aura.process import (
    CalledProcessError,
    ProcessError,
    TimeoutExpired,
    run_process,
)


CommandRunner = Callable[[Sequence[str]], str]
OC_CALL_BUDGET = 10


class OpenShiftCLIError(RuntimeError):
    """Raised when a read-only `oc` command fails."""


@dataclass
class _CallBudget:
    limit: int
    used: int = 0


_call_budget: ContextVar[_CallBudget | None] = ContextVar(
    "oc_call_budget",
    default=None,
)


@contextmanager
def oc_call_budget(limit: int = OC_CALL_BUDGET) -> Iterator[None]:
    """Limit the `oc` calls made inside the block, such as one command."""

    token = _call_budget.set(_CallBudget(limit))
    try:
        yield
    finally:
        _call_budget.reset(token)


def _spend_oc_call() -> None:
    budget = _call_budget.get()
    if budget is None:
        return
    if budget.used >= budget.limit:
        raise OpenShiftCLIError(f"oc call budget of {budget.limit} exceeded")
    budget.used += 1


def _run_command(args: Sequence[str]) -> str:
    result = run_process(args, env=None, timeout=30)
    if result.returncode != 0:
        raise CalledProcessError(
            result.returncode,
            list(args),
            output=result.stdout_text(),
            stderr=result.stderr_text(),
        )
    return result.stdout.decode("utf-8")


def _read_oc(
    command_runner: CommandRunner,
    args: Sequence[str],
    resource: str,
) -> dict[str, Any]:
    _spend_oc_call()
    try:
        output = command_runner(args)
    except CalledProcessError as error:
        output_text = str(error.stderr or error.stdout or "").strip()
        detail = output_text.splitlines()[-1] if output_text else "no output"
        raise OpenShiftCLIError(
            f"oc command failed for {resource} "
            f"(exit {error.returncode}): {detail}"
        ) from error
    except TimeoutExpired as error:
        raise OpenShiftCLIError(
            f"oc command timed out for {resource}"
        ) from error
    except ProcessError as error:
        raise OpenShiftCLIError(
            f"oc command timed out for {resource}"
            if error.timed_out
            else f"oc command failed for {resource}: {error.__cause__}"
        ) from error
    except OSError as error:
        raise OpenShiftCLIError(
            f"oc command failed for {resource}: {error}"
        ) from error
    except UnicodeDecodeError as error:
        raise OpenShiftCLIError(
            f"oc returned output that is not UTF-8 for {resource}"
        ) from error
    try:
        return json.loads(output)
    except json.JSONDecodeError as error:
        raise OpenShiftCLIError(
            f"oc returned invalid JSON for {resource}"
        ) from error


@final
class OpenShiftResource:
    """Cached read-only access to one Kubernetes resource type."""

    def __init__(
        self,
        *,
        command_runner: CommandRunner,
        kubeconfig_path: Path,
        resource: str,
        namespace: str | None,
        all_namespaces: bool,
    ) -> None:
        self._command_runner = command_runner
        self._kubeconfig_path = kubeconfig_path
        self._resource = resource
        self._namespace = namespace
        self._all_namespaces = all_namespaces
        self._response: dict[str, Any] | None = None

    def items(self) -> list[dict[str, Any]]:
        return self.response().get("items", [])

    def resource_version(self, default: str = "unknown") -> str:
        return (
            self.response()
            .get("metadata", {})
            .get("resourceVersion", default)
        )

    def response(self) -> dict[str, Any]:
        if self._response is None:
            self._response = self._get()
        return self._response

    def _get(self) -> dict[str, Any]:
        args = [
            "oc",
            "--kubeconfig",
            str(self._kubeconfig_path),
            "get",
            self._resource,
        ]
        if self._all_namespaces:
            args.append("-A")
        elif self._namespace is not None:
            args.extend(["-n", self._namespace])
        args.extend(["-o", "json"])
        return _read_oc(
            self._command_runner,
            args,
            self._resource,
        )


@final
class OpenShiftCLI:
    """Creates cached read-only resource clients backed by `oc`."""

    def __init__(
        self,
        *,
        kubeconfig_path: Path,
        command_runner: CommandRunner | None = None,
    ) -> None:
        self.kubeconfig_path = kubeconfig_path
        self._command_runner = command_runner or _run_command

    def resource(
        self,
        name: str,
        *,
        namespace: str | None = None,
        all_namespaces: bool = False,
    ) -> OpenShiftResource:
        if all_namespaces and namespace is not None:
            raise ValueError("cannot combine all namespaces with a namespace")
        return OpenShiftResource(
            command_runner=self._command_runner,
            kubeconfig_path=self.kubeconfig_path,
            resource=name,
            namespace=namespace,
            all_namespaces=all_namespaces,
        )


def cli_resources(
    kubeconfig_path: Path,
    command_runner: CommandRunner | None,
    specs: dict[str, dict[str, Any]],
) -> dict[str, OpenShiftResource]:
    """Build named resources from {name: OpenShiftCLI.resource options}."""

    cli = OpenShiftCLI(
        kubeconfig_path=kubeconfig_path,
        command_runner=command_runner,
    )
    return {
        name: cli.resource(name, **options)
        for name, options in specs.items()
    }


class OpenShiftCLIResourceClient:
    """Base client for a set of cached OpenShift resources.

    Subclasses name their resources in resource_specs as
    {name: OpenShiftCLI.resource options}.
    """

    resource_specs: dict[str, dict[str, Any]] = {}

    def __init__(
        self,
        *,
        kubeconfig_path: Path,
        command_runner: CommandRunner | None = None,
    ) -> None:
        self._resources = cli_resources(
            kubeconfig_path,
            command_runner,
            self.resource_specs,
        )

    def resource_version(self) -> str:
        return ";".join(
            f"{name}={resource.resource_version()}"
            for name, resource in self._resources.items()
        )

    def _items(self, name: str) -> list[dict[str, Any]]:
        return self._resources[name].items()


__all__ = [
    "CommandRunner",
    "OpenShiftCLI",
    "OpenShiftCLIError",
    "OpenShiftResource",
    "OpenShiftCLIResourceClient",
    "OC_CALL_BUDGET",
    "cli_resources",
    "oc_call_budget",
]
