"""Aura's configuration file.

Phase 3 commands take store locations and adapter settings only from this
file, never from a CLI flag. It is TOML at $XDG_CONFIG_HOME/aura/config.toml
(default ~/.config/aura/config.toml); the AURA_CONFIG environment variable
names another file.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
)


ADAPTERS = ("git_handoff", "git_pr", "tekton", "recording")


class ConfigError(ValueError):
    """Raised when the configuration file is missing or invalid."""


def _absolute(value: Path) -> Path:
    if not value.is_absolute():
        raise ValueError("must be an absolute path")
    return value


def _not_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("must not be blank")
    return value


class SQLiteStore(BaseModel):
    """A local execution store for development and tests."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    backend: Literal["sqlite"]
    path: Path
    # PostgreSQL takes the login from CURRENT_USER; SQLite has none.
    login: str = Field(min_length=1)

    _path = field_validator("path")(_absolute)
    _login = field_validator("login")(_not_blank)


class PostgreSQLStore(BaseModel):
    """The shared execution store; the password is read from a file."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    backend: Literal["postgresql"]
    host: str = Field(min_length=1)
    port: int = Field(default=5432, ge=1, le=65535)
    database: str = Field(min_length=1)
    user: str = Field(min_length=1)
    password_file: Path

    _password_file = field_validator("password_file")(_absolute)
    _text = field_validator("host", "database", "user")(_not_blank)


class ExecutionSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    adapters: list[Literal[ADAPTERS]] = Field(default_factory=list)  # type: ignore[valid-type]
    adapter_timeout_seconds: int = Field(default=300, gt=0)

    @field_validator("adapters")
    @classmethod
    def reject_repeats(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("must not repeat an adapter")
        return value


class GitIdentity(BaseModel):
    """The author of commits in a Git hand-off mailbox."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    author_name: str = Field(min_length=1)
    author_email: str = Field(pattern=r"^[^@\s]+@[^@\s]+$")

    _name = field_validator("author_name")(_not_blank)


class AuraConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    store: Annotated[
        SQLiteStore | PostgreSQLStore, Field(discriminator="backend")
    ]
    execution: ExecutionSettings = Field(default_factory=ExecutionSettings)
    git: GitIdentity | None = None


def config_path() -> Path:
    """Return the configuration file's path; a blank variable is an error."""

    for variable in ("AURA_CONFIG", "XDG_CONFIG_HOME"):
        value = os.environ.get(variable)
        if value is not None and not value.strip():
            raise ConfigError(f"{variable} is blank")
    if named := os.environ.get("AURA_CONFIG"):
        return Path(named)
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "aura" / "config.toml"


def load_config(path: Path | None = None) -> AuraConfig:
    path = path or config_path()
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise ConfigError(f"cannot read {path}: {error}") from error
    try:
        document = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"{path} is not valid TOML: {error}") from error
    try:
        return AuraConfig.model_validate(document)
    except ValidationError as error:
        raise ConfigError(f"{path} is invalid: {error}") from error


__all__ = [
    "ADAPTERS",
    "AuraConfig",
    "ConfigError",
    "ExecutionSettings",
    "GitIdentity",
    "PostgreSQLStore",
    "SQLiteStore",
    "config_path",
    "load_config",
]
