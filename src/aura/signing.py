"""SSH signatures on Phase 3 approvals and execution requests (D7).

Signing and verification run `ssh-keygen -Y` through run_process. A
signature is accepted only from a key that the allowed-signers list names
for exactly one principal and for the signature's namespace, at the time
the caller gives, so a key retired with `valid-before` keeps verifying
records signed earlier.
"""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from aura.process import ProcessError, ProcessResult, run_process


APPROVE = "aura-approve"
EXECUTE = "aura-execute"
NAMESPACES = (APPROVE, EXECUTE)
# A signed record may be stamped at most this far ahead of the reader.
CLOCK_MARGIN = timedelta(minutes=5)
SSH_KEYGEN_TIMEOUT = 30


class SignatureError(ValueError):
    """Raised when a record cannot be signed or its signature is refused."""


def sign(
    payload: bytes,
    *,
    key: Path,
    namespace: str,
    agent: bool = False,
) -> str:
    """Sign payload; with agent, key is a public key loaded in ssh-agent."""

    _require_namespace(namespace)
    command = ["ssh-keygen", "-Y", "sign", "-f", str(key), "-n", namespace]
    if agent:
        command.insert(3, "-U")
    result = _ssh_keygen(command, payload, env=None)
    if result.returncode != 0:
        raise SignatureError(f"cannot sign with {key}: {_last_line(result)}")
    return result.stdout_text()


def verify(
    payload: bytes,
    signature: str,
    *,
    principal: str,
    namespace: str,
    signers: str,
    at: datetime,
) -> str:
    """Return principal when signature is a valid namespace signature of
    payload by a key listed for principal alone at time at."""

    _require_namespace(namespace)
    _require_unshared_keys(signers, principal)
    verify_time = at.astimezone(UTC).strftime("%Y%m%d%H%M%S")
    with tempfile.TemporaryDirectory() as temp:
        allowed = Path(temp) / "allowed_signers"
        allowed.write_text(signers, encoding="utf-8")
        signature_file = Path(temp) / "payload.sig"
        signature_file.write_text(signature, encoding="utf-8")
        common = ["-f", str(allowed), "-O", f"verify-time={verify_time}"]
        found = _ssh_keygen(
            ["ssh-keygen", "-Y", "find-principals", "-s", str(signature_file), *common],
            None,
        )
        principals = found.stdout_text().split()
        if found.returncode != 0 or not principals:
            raise SignatureError("signature is from no listed key")
        if principals != [principal]:
            raise SignatureError(
                f"signing key belongs to {principals[0]}, not {principal}"
            )
        checked = _ssh_keygen(
            [
                "ssh-keygen", "-Y", "verify",
                "-I", principal,
                "-n", namespace,
                "-s", str(signature_file),
                *common,
            ],
            payload,
        )
    if checked.returncode != 0:
        raise SignatureError(
            f"{namespace} signature for {principal} is invalid: "
            f"{_last_line(checked)}"
        )
    return principal


def _require_unshared_keys(signers: str, principal: str) -> None:
    """Refuse when a key of principal is also listed for another one, so
    one key cannot count as two approvers (D11). find-principals reports
    only the first principal that lists a key."""

    owners: dict[str, set[str]] = {}
    for line in signers.splitlines():
        fields = line.split()
        if not fields or fields[0].startswith("#"):
            continue
        key_type = next(
            (index for index, field in enumerate(fields)
             if field.startswith(("ssh-", "ecdsa-", "sk-"))),
            None,
        )
        if key_type is None or key_type + 1 >= len(fields):
            raise SignatureError("allowed-signers line has no public key")
        blob = fields[key_type + 1]
        names = fields[0].split(",")
        options = " ".join(fields[1:key_type])
        if principal in names and 'namespaces="' not in options:
            # ssh-keygen accepts any namespace without it; D7 requires it.
            raise SignatureError(
                f"a key of {principal} has no namespaces option"
            )
        owners.setdefault(blob, set()).update(names)
    for names in owners.values():
        if principal in names and len(names) > 1:
            raise SignatureError(
                f"a key of {principal} is listed for more than one principal"
            )


def require_not_future(created_at: datetime, *, now: datetime) -> None:
    if created_at > now + CLOCK_MARGIN:
        raise SignatureError(
            f"created_at {created_at.isoformat()} is in the future"
        )


def _require_namespace(namespace: str) -> None:
    if namespace not in NAMESPACES:
        raise ValueError(f"unknown signature namespace: {namespace}")


def _ssh_keygen(
    command: list[str],
    payload: bytes | None,
    *,
    env: dict[str, str] | None = None,
) -> ProcessResult:
    try:
        # env=None keeps SSH_AUTH_SOCK, which signing through ssh-agent needs.
        return run_process(
            command,
            env=env,
            timeout=SSH_KEYGEN_TIMEOUT,
            input=b"" if payload is None else payload,
        )
    except ProcessError as error:
        raise SignatureError(str(error)) from error


def _last_line(result: ProcessResult) -> str:
    lines = result.stderr_text().strip().splitlines()
    return lines[-1] if lines else f"exit {result.returncode}"


__all__ = [
    "APPROVE",
    "CLOCK_MARGIN",
    "EXECUTE",
    "NAMESPACES",
    "SignatureError",
    "require_not_future",
    "sign",
    "verify",
]
