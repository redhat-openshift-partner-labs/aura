"""Run external programs through one chokepoint.

No other Aura module imports subprocess; tests/test_architecture.py enforces
it. CalledProcessError and TimeoutExpired are re-exported because injected
command runners report failures with them.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from subprocess import CalledProcessError, TimeoutExpired


class ProcessError(Exception):
    """Raised when a program cannot start or exceeds its timeout."""

    def __init__(self, message: str, *, timed_out: bool) -> None:
        super().__init__(message)
        self.timed_out = timed_out


@dataclass(frozen=True)
class ProcessResult:
    returncode: int
    stdout: bytes
    stderr: bytes

    def stdout_text(self) -> str:
        return self.stdout.decode("utf-8", "replace")

    def stderr_text(self) -> str:
        return self.stderr.decode("utf-8", "replace")


def run_process(
    argv: Sequence[str],
    *,
    env: Mapping[str, str] | None,
    timeout: float,
    cwd: Path | None = None,
    input: bytes | None = None,
) -> ProcessResult:
    """Run a program to completion; a non-zero exit is a result, not an error.

    stdin is closed unless input is given. env is required so every caller
    chooses its environment; None inherits Aura's.
    """

    try:
        completed = subprocess.run(
            list(argv),
            input=input,
            stdin=subprocess.DEVNULL if input is None else None,
            capture_output=True,
            env=env,
            cwd=cwd,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise ProcessError(
            f"{argv[0]} timed out after {timeout:g} seconds",
            timed_out=True,
        ) from error
    except OSError as error:
        # subprocess names a missing working directory in filename, which
        # otherwise names the program.
        if cwd is not None and error.filename in (cwd, os.fspath(cwd)):
            raise ProcessError(
                f"cannot use working directory {cwd} for {argv[0]}: "
                f"{error.strerror}",
                timed_out=False,
            ) from error
        raise ProcessError(
            f"cannot run {argv[0]}: {error}",
            timed_out=False,
        ) from error
    return ProcessResult(
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


__all__ = [
    "CalledProcessError",
    "ProcessError",
    "ProcessResult",
    "TimeoutExpired",
    "run_process",
]
