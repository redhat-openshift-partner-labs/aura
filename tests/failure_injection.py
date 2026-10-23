"""Record every Aura record write and optionally fail one of them."""

from __future__ import annotations

import errno
import io
from collections.abc import Callable, Sequence
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import UUID

import aura.command_store as command_store
import aura.json_store as json_store
from aura.cli import main
from aura.contracts import AuditEvent


_SAVE = json_store.save_private_json


@dataclass(frozen=True)
class RecordWrite:
    label: str
    path: Path


@dataclass(frozen=True)
class CliRun:
    exit_code: int
    stdout: str
    stderr: str
    writes: list[RecordWrite]


def write_label(record: object) -> str:
    """Name a record by type, and audit events by type and event type."""

    name = type(record).__name__
    if isinstance(record, AuditEvent):
        return f"{name}:{record.event_type.value}"
    return name


def install_write_faults(
    monkeypatch,
    fail_at: int | None = None,
) -> list[RecordWrite]:
    """Route every store write through a recorder that can fail one write.

    Writes are numbered from zero in call order. The write at fail_at raises
    the same OSError a full disk would; later writes succeed, so the
    command's own failure handling runs normally.
    """

    writes: list[RecordWrite] = []

    def save(root, path, record, duplicate_error, duplicate_message):
        index = len(writes)
        writes.append(RecordWrite(write_label(record), path))
        if index == fail_at:
            raise OSError(errno.ENOSPC, "injected write failure", str(path))
        return _SAVE(root, path, record, duplicate_error, duplicate_message)

    monkeypatch.setattr(json_store, "save_private_json", save)
    monkeypatch.setattr(command_store, "save_private_json", save)
    return writes


def run_cli(
    arguments: Sequence[str],
    monkeypatch,
    *,
    collector: object | None,
    command_id: UUID,
    clock: Callable[[], datetime],
    fail_at: int | None = None,
) -> CliRun:
    """Run the CLI with recorded writes and captured output."""

    writes = install_write_faults(monkeypatch, fail_at)
    stdout, stderr = io.StringIO(), io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        exit_code = main(
            list(arguments),
            collector=collector,
            command_id_factory=lambda: command_id,
            clock=clock,
        )
    return CliRun(exit_code, stdout.getvalue(), stderr.getvalue(), writes)
