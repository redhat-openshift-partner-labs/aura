"""`proposal status` reports the Phase 3 derived state (task 2.5)."""

from __future__ import annotations

import io
import json
from contextlib import redirect_stderr, redirect_stdout
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from aura.cli import HubAccess, main
from aura.collectors.hub_policy import HubPolicy, HubPolicyError
from aura.signing import APPROVE
from test_proposal_approve import approver_config, decide, policy
from test_proposal_submit import phase2, proposal_id, submit, use_store  # noqa: F401
from test_signing import key, listed


NOW = datetime(2026, 10, 30, 13, tzinfo=UTC)


def status(root: Path, *, hub: HubPolicy | None = None, kubeconfig: bool = True, unreadable: bool = False):
    def read(path: Path) -> HubPolicy:
        if unreadable:
            raise HubPolicyError("ConfigMap aura-allowed-signers not found in aura")
        return hub

    arguments = [
        "proposal", "status",
        "--fleet-clusters", str(root / "fleet-clusters"),
        "--proposal-dir", str(root / "proposal"),
        "--command-dir", str(root / "status-commands"),
        "--operator", "viewer@example.com",
        "--idempotency-key", f"status-{datetime.now().timestamp()}",
    ]
    if kubeconfig:
        arguments += ["--openshift-kubeconfig", "/kube/aura-readonly"]
    stdout, stderr = io.StringIO(), io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(arguments, clock=lambda: NOW, hub=HubAccess(policy=read))
    return code, stdout.getvalue(), stderr.getvalue()


def item(stdout: str) -> dict:
    (only,) = json.loads(stdout)["items"]
    return only["data"]


@pytest.fixture
def keys(tmp_path) -> tuple[Path, Path]:
    return key(tmp_path, "first"), key(tmp_path, "second")


def signers(first: Path, second: Path) -> str:
    return listed("approver@example.com", first, APPROVE) + listed(
        "second@example.com", second, APPROVE
    )


def approve_as(tmp_path, monkeypatch, database, root, private, principal, number, decision="approve"):
    config = tmp_path / f"{principal}.toml"
    config.write_text(
        f'[store]\nbackend = "sqlite"\npath = "{database}"\nlogin = "{principal}"\n\n'
        f'[signing]\nprincipal = "{principal}"\nkey = "{private}"\nagent = false\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("AURA_CONFIG", str(config))
    hub = policy(signers(*keys_of(tmp_path)))
    code, _, stderr = decide(
        root, decision, hub=hub, key_name=f"decide-{number}",
        command_id=UUID(int=600 + number),
    )
    assert code == 0, stderr


def keys_of(tmp_path: Path) -> tuple[Path, Path]:
    return tmp_path / "first", tmp_path / "second"


def test_without_a_kubeconfig_the_state_is_not_read(tmp_path, monkeypatch, phase2) -> None:
    use_store(tmp_path, monkeypatch)

    code, stdout, stderr = status(phase2, kubeconfig=False)

    assert code == 0, stderr
    assert (item(stdout)["published"], item(stdout)["derived_state"]) == (None, None)


def test_an_unpublished_proposal_is_ready(tmp_path, monkeypatch, phase2, keys) -> None:
    use_store(tmp_path, monkeypatch)

    code, stdout, stderr = status(phase2, hub=policy(signers(*keys)))

    assert code == 0, stderr
    assert (item(stdout)["published"], item(stdout)["derived_state"]) == (False, "proposal_ready")


def test_the_state_follows_the_approvals(tmp_path, monkeypatch, phase2, keys) -> None:
    database = use_store(tmp_path, monkeypatch, login="proposer-a")
    assert submit(phase2)[0] == 0
    hub = policy(signers(*keys))

    assert item(status(phase2, hub=hub)[1])["derived_state"] == "proposal_ready"

    approve_as(tmp_path, monkeypatch, database, phase2, keys[0], "approver@example.com", 1)
    code, stdout, stderr = status(phase2, hub=hub)
    assert code == 0, stderr
    # A deprovision needs two distinct approvers (D11).
    assert item(stdout)["derived_state"] == "awaiting_approval"

    approve_as(tmp_path, monkeypatch, database, phase2, keys[1], "second@example.com", 2)
    assert item(status(phase2, hub=hub)[1])["derived_state"] == "approved"

    approve_as(tmp_path, monkeypatch, database, phase2, keys[1], "second@example.com", 3, "reject")
    assert item(status(phase2, hub=hub)[1])["derived_state"] == "rejected"


def test_an_approval_the_hub_list_no_longer_accepts_is_unreadable(tmp_path, monkeypatch, phase2, keys) -> None:
    database = use_store(tmp_path, monkeypatch, login="proposer-a")
    assert submit(phase2)[0] == 0
    approve_as(tmp_path, monkeypatch, database, phase2, keys[0], "approver@example.com", 1)

    code, stdout, stderr = status(phase2, hub=policy(listed("second@example.com", keys[1], APPROVE)))

    assert (code, stdout) == (2, "")
    assert "signature is refused" in stderr


def test_a_hub_read_failure_fails_the_command(tmp_path, monkeypatch, phase2) -> None:
    use_store(tmp_path, monkeypatch)

    code, stdout, stderr = status(phase2, unreadable=True)

    assert (code, stdout) == (2, "")
    assert "aura-allowed-signers not found" in stderr
