from __future__ import annotations

import fcntl
import os
from collections.abc import Iterator
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
from typing import final
from uuid import UUID

from aura.contracts import CommandEnvelope, CommandState
from aura.json_store import parse_record, save_private_json


class CommandAlreadyStored(Exception):
    """Raised when saving a command with an already stored key."""


class CommandNotTerminal(ValueError):
    """Raised when saving a command that has not reached a terminal state."""


class CommandNotFound(Exception):
    """Raised when loading a command that does not exist."""


class CommandRecordUnreadable(Exception):
    """Raised when a stored command record cannot be read or validated."""


@final
class JsonCommandStore:
    """Stores terminal commands by idempotency key."""

    def __init__(self, root: Path) -> None:
        self.root = root

    @contextmanager
    def lock(self) -> Iterator[None]:
        """Serialize command execution within this store directory."""

        self.root.mkdir(parents=True, mode=0o700, exist_ok=True)
        self.root.chmod(0o700)
        descriptor = os.open(self.root, os.O_RDONLY)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def save(self, command: CommandEnvelope) -> Path:
        terminal_states = {
            CommandState.SUCCEEDED,
            CommandState.FAILED,
            CommandState.REFUSED,
        }
        if command.status not in terminal_states:
            raise CommandNotTerminal(
                "idempotency key record requires a terminal command: "
                f"{command.idempotency_key}"
            )
        path = self._path(command.idempotency_key)
        return save_private_json(
            self.root,
            path,
            command,
            lambda message: CommandAlreadyStored(message),
            f"idempotency key already succeeded: {command.idempotency_key}"
        )

    def load(self, idempotency_key: str) -> CommandEnvelope:
        unreadable = f"idempotency key record is unreadable: {idempotency_key}"
        try:
            command = _read_command(self._path(idempotency_key))
        except FileNotFoundError as error:
            raise CommandNotFound(
                f"idempotency key not found: {idempotency_key}"
            ) from error
        except (OSError, ValueError) as error:
            raise CommandRecordUnreadable(unreadable) from error
        if command.idempotency_key != idempotency_key:
            raise CommandRecordUnreadable(unreadable)
        return command

    def _path(self, idempotency_key: str) -> Path:
        digest = sha256(idempotency_key.encode("utf-8")).hexdigest()
        return self.root / f"{digest}.json"


def find_command(root: Path, command_id: UUID) -> CommandEnvelope:
    """Return a command's terminal record from a command store by its ID.

    Records are keyed by idempotency key, so this reads every record; an
    unreadable one is an error and is never skipped (I8).
    """

    try:
        paths = sorted(path for path in root.iterdir() if path.suffix == ".json")
    except FileNotFoundError as error:
        raise CommandNotFound(f"command not found: {command_id}") from error
    for path in paths:
        try:
            command = _read_command(path)
        except (OSError, ValueError) as error:
            raise CommandRecordUnreadable(
                f"command record is unreadable: {path.name}"
            ) from error
        if command.command_id == command_id:
            return command
    raise CommandNotFound(f"command not found: {command_id}")


def _read_command(path: Path) -> CommandEnvelope:
    return parse_record(
        path.read_text(encoding="utf-8"),
        CommandEnvelope,
        "idempotency key",
    )


__all__ = [
    "CommandAlreadyStored",
    "CommandNotTerminal",
    "CommandNotFound",
    "CommandRecordUnreadable",
    "JsonCommandStore",
    "find_command",
]
