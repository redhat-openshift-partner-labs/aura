from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

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
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        descriptor = os.open(path, flags, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                descriptor = -1
                stream.write(record.model_dump_json(indent=2))
        finally:
            if descriptor >= 0:
                os.close(descriptor)
    except FileExistsError as error:
        raise duplicate_error(duplicate_message) from error
    return path


__all__ = [
    "save_private_json",
]
