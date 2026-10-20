from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel


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
    _fsync_directory(root)
    return path


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


__all__ = [
    "save_private_json",
]
