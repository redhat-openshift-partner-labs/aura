"""Evidence for a new-cluster scaffold proposal."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, final
from uuid import UUID, uuid5

from aura.collectors.openshift_inventory import OpenShiftInventoryClient
from aura.contracts import EvidenceItem, EvidenceSnapshot
from aura.scaffold import ScaffoldCheckoutError, ScaffoldRequest


class SnapshotCollector(Protocol):
    def collect(
        self,
        *,
        command_id: UUID,
        snapshot_id: UUID | None = None,
        collected_at: datetime | None = None,
        source_version: str | None = None,
    ) -> EvidenceSnapshot:
        ...


@final
class ScaffoldEvidenceCollector:
    """Collects every name a new cluster must not reuse, the request, the
    pinned scaffold checkout, and the kustomize version."""

    collector_version = "0.1.0"

    def __init__(
        self,
        *,
        request: ScaffoldRequest,
        git_collector: SnapshotCollector,
        openshift_client: OpenShiftInventoryClient,
        scaffold_checkout: Path,
        kustomize_version: Callable[[], str | None],
    ) -> None:
        self._request = request
        self._git = git_collector
        self._openshift = openshift_client
        self._scaffold_checkout = scaffold_checkout
        self._kustomize_version = kustomize_version

    def collect(
        self,
        *,
        command_id: UUID,
        snapshot_id: UUID | None = None,
        collected_at: datetime | None = None,
        source_version: str | None = None,
    ) -> EvidenceSnapshot:
        collected_at = collected_at or datetime.now(UTC)
        git = self._git.collect(command_id=command_id, collected_at=collected_at)
        openshift_version = self._openshift.resource_version()
        names = {
            "cluster_deployments": _names(
                self._openshift.list_cluster_deployments()
            ),
            "managed_clusters": _names(self._openshift.list_managed_clusters()),
        }
        top = _git(self._scaffold_checkout, "rev-parse", "--show-toplevel")
        if Path(top.strip()).resolve() != self._scaffold_checkout.resolve():
            raise ScaffoldCheckoutError(
                f"scaffold checkout {self._scaffold_checkout} is not the top "
                "of a Git checkout"
            )
        commit = _git(self._scaffold_checkout, "rev-parse", "HEAD").strip()
        changes = _git(
            self._scaffold_checkout,
            "status",
            "--porcelain",
            "--untracked-files=no",
        )

        def item(kind: str, data: dict[str, Any]) -> EvidenceItem:
            return EvidenceItem(
                item_id=uuid5(command_id, f"scaffold:{kind}"),
                kind=kind,
                data=data,
            )

        return EvidenceSnapshot(
            snapshot_id=snapshot_id or uuid5(command_id, "scaffold"),
            command_id=command_id,
            collector="scaffold",
            collector_version=self.collector_version,
            collected_at=collected_at,
            source=git.source,
            source_version=source_version
            or (
                f"git={git.source_version};"
                f"openshift={openshift_version};scaffold={commit}"
            ),
            items=[
                *git.items,
                item("openshift_cluster_names", names),
                item("scaffold_request", self._request.model_dump(mode="json")),
                item(
                    "scaffold_checkout",
                    {"commit": commit, "clean": not changes.strip()},
                ),
                item("kustomize", {"version": self._kustomize_version()}),
            ],
            redactions=sorted(git.redactions),
        )


def _names(resources: list[dict[str, Any]]) -> list[str]:
    return sorted({str(resource["metadata"]["name"]) for resource in resources})


def _git(root: Path, *arguments: str) -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(root), *arguments],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    except subprocess.CalledProcessError as error:
        detail = (error.stderr or "").strip().splitlines()
        raise ScaffoldCheckoutError(
            f"scaffold checkout {root} is not usable: "
            f"{detail[-1] if detail else 'git failed'}"
        ) from error
    except OSError as error:
        raise ScaffoldCheckoutError(
            f"scaffold checkout {root} is not usable: {error}"
        ) from error


__all__ = ["ScaffoldEvidenceCollector"]
