from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import ClassVar, Generic, TypeVar, cast
from uuid import UUID, uuid4

from pydantic import BaseModel


R = TypeVar("R", bound=BaseModel)


def save_private_json(
    root: Path,
    path: Path,
    record: BaseModel,
    duplicate_error: Callable[[str], Exception],
    duplicate_message: str,
) -> Path:
    """Save a private JSON record while rejecting duplicate records."""

    root.mkdir(parents=True, exist_ok=True)
    root.chmod(0o700)
    temporary_path = path.with_name(
        f".{path.name}.{uuid4().hex}.tmp"
    )
    try:
        descriptor = os.open(
            temporary_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                descriptor = -1
                stream.write(record.model_dump_json(indent=2))
                stream.flush()
                os.fsync(stream.fileno())
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        try:
            os.link(temporary_path, path)
        except FileExistsError as error:
            raise duplicate_error(duplicate_message) from error
    except FileExistsError as error:
        raise duplicate_error(duplicate_message) from error
    finally:
        temporary_path.unlink(missing_ok=True)
    try:
        _fsync_directory(root)
    except OSError:
        # Callers treat a raised write as not stored; keep that true.
        path.unlink(missing_ok=True)
        raise
    return path


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class JsonRecordStore(Generic[R]):
    """Immutable JSON records keyed by one identifier field."""

    record_type: ClassVar[type[BaseModel]]
    record_label: ClassVar[str]
    id_field: ClassVar[str]
    already_stored: ClassVar[type[Exception]]
    not_found: ClassVar[type[Exception]]

    def __init__(self, root: Path) -> None:
        self.root = root

    def save(self, record: R) -> Path:
        record_id = getattr(record, self.id_field)
        return save_private_json(
            self.root,
            self._path(record_id),
            record,
            self.already_stored,
            f"{self.record_label} already stored: {record_id}",
        )

    def load(self, record_id: UUID) -> R:
        try:
            value = self._path(record_id).read_text(encoding="utf-8")
        except FileNotFoundError as error:
            raise self.not_found(
                f"{self.record_label} not found: {record_id}"
            ) from error
        return cast(R, self.record_type.model_validate_json(value))

    def record_ids(self) -> list[UUID]:
        """Return stored record IDs in ascending order.

        Unlike glob, iterdir raises when the store is missing or unreadable.
        """

        record_ids: list[UUID] = []
        for path in self.root.iterdir():
            if path.suffix != ".json":
                continue
            try:
                record_ids.append(UUID(path.stem))
            except ValueError as error:
                raise ValueError(
                    f"record name is not an ID: {path.name}"
                ) from error
        return sorted(record_ids)

    def _path(self, record_id: UUID) -> Path:
        return self.root / f"{record_id}.json"


__all__ = [
    "JsonRecordStore",
    "save_private_json",
]
