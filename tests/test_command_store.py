from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest

from aura.command_store import (
    CommandAlreadyStored,
    CommandNotTerminal,
    CommandNotFound,
    JsonCommandStore,
)
from aura.contracts import CommandEnvelope, CommandState, CommandType


BASE_COMMAND = CommandEnvelope(
    command_id=UUID(int=1),
    command_type=CommandType.INVENTORY_LIST,
    operator_id="operator@example.com",
    created_at=datetime(2026, 1, 1, tzinfo=UTC),
    input={"query": "list inventory"},
    idempotency_key="test-key",
    status=CommandState.SUCCEEDED,
)


def make_command(idempotency_key: str) -> CommandEnvelope:
    return BASE_COMMAND.model_copy(
        update={"idempotency_key": idempotency_key}
    )


def test_json_command_store_saves_and_loads_by_key(tmp_path) -> None:
    command = make_command("test-key")
    store = JsonCommandStore(tmp_path)

    store.save(command)

    assert store.load("test-key") == command
    with pytest.raises(CommandNotFound):
        store.load("missing-key")


def test_json_command_store_rejects_duplicate_key(tmp_path) -> None:
    store = JsonCommandStore(tmp_path)
    store.save(make_command("test-key"))
    replacement = make_command("test-key").model_copy(
        update={"command_id": UUID(int=2)}
    )

    with pytest.raises(CommandAlreadyStored):
        store.save(replacement)


def test_json_command_store_rejects_non_terminal_command(tmp_path) -> None:
    store = JsonCommandStore(tmp_path)
    command = make_command("test-key").with_status(
        CommandState.COLLECTING_EVIDENCE
    )

    with pytest.raises(CommandNotTerminal):
        store.save(command)


@pytest.mark.parametrize(
    "status",
    [
        CommandState.SUCCEEDED,
        CommandState.FAILED,
        CommandState.REFUSED,
    ],
)
def test_json_command_store_saves_terminal_commands(
    tmp_path,
    status: CommandState,
) -> None:
    store = JsonCommandStore(tmp_path)
    command = make_command("test-key").with_status(status)

    path = store.save(command)

    assert store.load("test-key") == command
    assert path.is_file()


