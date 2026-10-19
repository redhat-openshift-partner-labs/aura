from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, final


CommandRunner = Callable[[Sequence[str]], str]


def _run_command(args: Sequence[str]) -> str:
    result = subprocess.run(
        args,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


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
        return json.loads(self._command_runner(args))


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


class OpenShiftCLIResourceClient:
    """Base client for a set of cached OpenShift resources."""

    def __init__(self, resources: dict[str, OpenShiftResource]) -> None:
        self._resources = resources

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
    "OpenShiftResource",
    "OpenShiftCLIResourceClient",
]
