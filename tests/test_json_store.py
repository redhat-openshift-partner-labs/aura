from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import BaseModel

import aura.json_store as json_store
from aura.json_store import save_private_json


class Record(BaseModel):
    value: str


def duplicate(message: str) -> Exception:
    return RuntimeError(message)


def test_save_private_json_rejects_existing_record(
    tmp_path: Path,
) -> None:
    path = tmp_path / "record.json"
    path.write_text("existing", encoding="utf-8")

    with pytest.raises(RuntimeError, match="already stored"):
        save_private_json(
            tmp_path,
            path,
            Record(value="new"),
            duplicate,
            "record already stored",
        )

    assert path.read_text(encoding="utf-8") == "existing"


def test_save_private_json_flushes_record_to_disk(
    tmp_path: Path,
    monkeypatch,
) -> None:
    synced: list[int] = []
    monkeypatch.setattr(
        json_store.os,
        "fsync",
        lambda descriptor: synced.append(descriptor),
    )
    path = tmp_path / "record.json"

    result = save_private_json(
        tmp_path,
        path,
        Record(value="new"),
        duplicate,
        "record already stored",
    )

    assert result == path
    assert path.read_text(encoding="utf-8")
    assert synced


def test_save_private_json_leaves_no_partial_record_on_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    def failing_dump(self: Record, **kwargs) -> str:
        raise RuntimeError("serialization failed")

    monkeypatch.setattr(Record, "model_dump_json", failing_dump)
    path = tmp_path / "record.json"

    with pytest.raises(RuntimeError, match="serialization failed"):
        save_private_json(
            tmp_path,
            path,
            Record(value="new"),
            duplicate,
            "record already stored",
        )

    assert list(tmp_path.iterdir()) == []
