from __future__ import annotations

import fcntl
import os
from collections.abc import Iterator
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
from typing import final

from aura.contracts import CommandEnvelope, CommandState
from aura.json_store import save_private_json


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
        path = self._path(idempotency_key)
        try:
            command = _read_command(path)
        except FileNotFoundError as error:
            raise CommandNotFound(
                "idempotency key not found: "
                f"{idempotency_key}"
            ) from error
        except (OSError, ValueError) as error:
            raise CommandRecordUnreadable(
                "idempotency key record is unreadable: "
                f"{idempotency_key}"
            ) from error
        if command.idempotency_key != idempotency_key:
            raise CommandNotFound(
                "idempotency key not found: "
                f"{idempotency_key}"
            )
        return command

    def _path(self, idempotency_key: str) -> Path:
        digest = sha256(idempotency_key.encode("utf-8")).hexdigest()
        return self.root / f"{digest}.json"


def _read_command(path: Path) -> CommandEnvelope:
    return CommandEnvelope.model_validate_json(
        path.read_text(encoding="utf-8")
    )


__all__ = [
    "CommandAlreadyStored",
    "CommandNotTerminal",
    "CommandNotFound",
    "CommandRecordUnreadable",
    "JsonCommandStore",
]
