from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import final

from aura.contracts import CommandEnvelope
from aura.json_store import save_private_json


class CommandAlreadyStored(Exception):
    """Raised when saving a command with an already stored key."""


class CommandNotFound(Exception):
    """Raised when loading a command that does not exist."""


@final
class JsonCommandStore:
    """Stores successfully completed commands by idempotency key."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def save(self, command: CommandEnvelope) -> Path:
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
    "CommandNotFound",
    "JsonCommandStore",
]
