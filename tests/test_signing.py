"""SSH signatures on approvals and execution requests (Phase 3 task 2.3)."""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from aura.signing import (
    APPROVE,
    EXECUTE,
    SignatureError,
    require_not_future,
    sign,
    verify,
)


AT = datetime(2026, 10, 29, 12, tzinfo=UTC)
PAYLOAD = b'{"proposal_id":"example"}'


def key(tmp_path: Path, name: str) -> Path:
    path = tmp_path / name
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", name, "-f", str(path)],
        check=True,
    )
    return path


def listed(principal: str, private: Path, namespaces: str, **options: str) -> str:
    public = private.with_suffix(".pub").read_text(encoding="utf-8").strip()
    extra = "".join(f',{name}="{value}"' for name, value in options.items())
    return f'{principal} namespaces="{namespaces}"{extra} {public}\n'


@pytest.fixture
def approver(tmp_path) -> Path:
    return key(tmp_path, "approver")


def signers(*lines: str) -> str:
    return "".join(lines)


def test_a_valid_signature_verifies(approver) -> None:
    signature = sign(PAYLOAD, key=approver, namespace=APPROVE)

    assert signature.startswith("-----BEGIN SSH SIGNATURE-----\n")
    assert verify(
        PAYLOAD,
        signature,
        principal="approver@example.com",
        namespace=APPROVE,
        signers=signers(listed("approver@example.com", approver, APPROVE)),
        at=AT,
    ) == "approver@example.com"


def check(approver: Path, signature: str, allowed: str, **changes) -> None:
    options = {
        "principal": "approver@example.com",
        "namespace": APPROVE,
        "signers": allowed,
        "at": AT,
        **changes,
    }
    payload = options.pop("payload", PAYLOAD)
    verify(payload, signature, **options)


def test_a_wrong_principal_is_refused(approver) -> None:
    signature = sign(PAYLOAD, key=approver, namespace=APPROVE)
    allowed = signers(listed("approver@example.com", approver, APPROVE))

    with pytest.raises(SignatureError, match="other@example.com"):
        check(approver, signature, allowed, principal="other@example.com")


def test_a_signature_in_another_namespace_is_refused(approver) -> None:
    signature = sign(PAYLOAD, key=approver, namespace=EXECUTE)
    allowed = signers(listed("approver@example.com", approver, f"{APPROVE},{EXECUTE}"))

    with pytest.raises(SignatureError, match="namespace"):
        check(approver, signature, allowed)


def test_a_key_not_allowed_the_namespace_is_refused(approver) -> None:
    signature = sign(PAYLOAD, key=approver, namespace=APPROVE)
    allowed = signers(listed("approver@example.com", approver, EXECUTE))

    with pytest.raises(SignatureError):
        check(approver, signature, allowed)


def test_a_tampered_payload_is_refused(approver) -> None:
    signature = sign(PAYLOAD, key=approver, namespace=APPROVE)
    allowed = signers(listed("approver@example.com", approver, APPROVE))

    with pytest.raises(SignatureError):
        check(approver, signature, allowed, payload=PAYLOAD + b" ")


def test_an_unlisted_key_is_refused(tmp_path, approver) -> None:
    other = key(tmp_path, "other")
    signature = sign(PAYLOAD, key=other, namespace=APPROVE)
    allowed = signers(listed("approver@example.com", approver, APPROVE))

    with pytest.raises(SignatureError, match="no listed key"):
        check(approver, signature, allowed)


def test_a_key_listed_for_two_principals_is_refused(approver) -> None:
    signature = sign(PAYLOAD, key=approver, namespace=APPROVE)
    allowed = signers(
        listed("approver@example.com", approver, APPROVE),
        listed("second@example.com", approver, APPROVE),
    )

    with pytest.raises(SignatureError, match="more than one principal"):
        check(approver, signature, allowed)


def test_a_retired_key_verifies_only_before_its_retirement(approver) -> None:
    signature = sign(PAYLOAD, key=approver, namespace=APPROVE)
    allowed = signers(
        listed("approver@example.com", approver, APPROVE, **{"valid-before": "20261029"})
    )

    assert check(approver, signature, allowed, at=AT - timedelta(days=1)) is None
    with pytest.raises(SignatureError):
        check(approver, signature, allowed, at=AT)


def test_two_keys_of_one_principal_name_one_approver(tmp_path, approver) -> None:
    second = key(tmp_path, "second")
    allowed = signers(
        listed("approver@example.com", approver, APPROVE),
        listed("approver@example.com", second, APPROVE),
    )

    principals = {
        verify(
            PAYLOAD,
            sign(PAYLOAD, key=private, namespace=APPROVE),
            principal="approver@example.com",
            namespace=APPROVE,
            signers=allowed,
            at=AT,
        )
        for private in (approver, second)
    }

    assert principals == {"approver@example.com"}


def test_an_unknown_namespace_is_refused(approver) -> None:
    with pytest.raises(ValueError, match="namespace"):
        sign(PAYLOAD, key=approver, namespace="git")


def test_a_missing_key_is_a_signature_error(tmp_path) -> None:
    with pytest.raises(SignatureError, match="cannot sign"):
        sign(PAYLOAD, key=tmp_path / "missing", namespace=APPROVE)


@pytest.mark.parametrize(
    ("offset", "allowed"),
    [
        (timedelta(minutes=5), True),
        (timedelta(minutes=5, seconds=1), False),
        (timedelta(hours=-3), True),
    ],
    ids=["at-margin", "beyond-margin", "past"],
)
def test_a_record_created_in_the_future_is_refused(offset, allowed) -> None:
    if allowed:
        require_not_future(AT + offset, now=AT)
    else:
        with pytest.raises(SignatureError, match="in the future"):
            require_not_future(AT + offset, now=AT)


def test_a_key_listed_for_a_principal_list_is_refused(approver) -> None:
    signature = sign(PAYLOAD, key=approver, namespace=APPROVE)
    allowed = listed("approver@example.com,second@example.com", approver, APPROVE)

    with pytest.raises(SignatureError, match="more than one principal"):
        check(approver, signature, allowed)


def test_a_signer_line_without_a_key_is_refused(approver) -> None:
    signature = sign(PAYLOAD, key=approver, namespace=APPROVE)
    allowed = signers(
        listed("approver@example.com", approver, APPROVE),
        'broken@example.com namespaces="aura-approve"\n',
    )

    with pytest.raises(SignatureError, match="no public key"):
        check(approver, signature, allowed)


def test_a_key_without_a_namespaces_option_is_refused(approver) -> None:
    signature = sign(PAYLOAD, key=approver, namespace=APPROVE)
    public = approver.with_suffix(".pub").read_text(encoding="utf-8").strip()

    # Without the option ssh-keygen accepts any namespace; D7 requires it.
    with pytest.raises(SignatureError, match="namespaces"):
        check(approver, signature, f"approver@example.com {public}\n")


def test_signing_through_ssh_agent_names_the_public_key(monkeypatch) -> None:
    import aura.signing as signing
    from aura.process import ProcessResult

    calls: list[tuple[list[str], object]] = []

    def run(argv, *, env, timeout, input):
        calls.append((list(argv), env))
        return ProcessResult(0, b"-----BEGIN SSH SIGNATURE-----\nAAAA\n-----END SSH SIGNATURE-----\n", b"")

    monkeypatch.setattr(signing, "run_process", run)

    sign(PAYLOAD, key=Path("/home/me/.ssh/id_ed25519.pub"), namespace=APPROVE, agent=True)

    # -U signs with the agent's private key; env=None keeps SSH_AUTH_SOCK.
    assert calls == [(
        ["ssh-keygen", "-Y", "sign", "-U", "-f", "/home/me/.ssh/id_ed25519.pub", "-n", APPROVE],
        None,
    )]
