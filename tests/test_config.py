"""Aura's configuration file (Phase 3 task 2.1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from aura.config import (
    AuraConfig,
    ConfigError,
    PostgreSQLStore,
    SQLiteStore,
    config_path,
    load_config,
)


SQLITE = """
[store]
backend = "sqlite"
path = "/var/lib/aura/aura.db"
login = "operator-a"
"""

POSTGRESQL = """
[store]
backend = "postgresql"
host = "aura-postgresql.aura-db.svc"
port = 5432
database = "aura"
user = "operator-a"
password_file = "/home/operator/.config/aura/password"

[execution]
adapters = ["tekton"]
adapter_timeout_seconds = 120

[git]
author_name = "Aura"
author_email = "aura@example.com"
"""


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_aura_config_names_the_file(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AURA_CONFIG", str(tmp_path / "elsewhere.toml"))

    assert config_path() == tmp_path / "elsewhere.toml"


def test_default_path_follows_xdg_config_home(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("AURA_CONFIG", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

    assert config_path() == tmp_path / "xdg" / "aura" / "config.toml"


def test_default_path_without_xdg_is_under_home(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("AURA_CONFIG", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))

    assert config_path() == tmp_path / ".config" / "aura" / "config.toml"


@pytest.mark.parametrize("variable", ["AURA_CONFIG", "XDG_CONFIG_HOME"])
def test_a_blank_path_variable_is_an_error(monkeypatch, variable) -> None:
    monkeypatch.delenv("AURA_CONFIG", raising=False)
    monkeypatch.setenv(variable, " ")

    with pytest.raises(ConfigError, match=f"{variable} is blank"):
        config_path()


def test_sqlite_config_loads_with_defaults(tmp_path) -> None:
    config = load_config(write(tmp_path, SQLITE))

    assert isinstance(config, AuraConfig)
    assert config.store == SQLiteStore(
        backend="sqlite",
        path=Path("/var/lib/aura/aura.db"),
        login="operator-a",
    )
    assert config.execution.adapters == []
    assert config.execution.adapter_timeout_seconds == 300
    assert config.git is None


def test_postgresql_config_loads(tmp_path) -> None:
    config = load_config(write(tmp_path, POSTGRESQL))

    assert isinstance(config.store, PostgreSQLStore)
    assert config.store.database == "aura"
    assert config.execution.adapters == ["tekton"]
    assert config.git is not None
    assert config.git.author_email == "aura@example.com"


def test_a_missing_file_is_an_error(tmp_path) -> None:
    missing = tmp_path / "missing.toml"

    with pytest.raises(ConfigError, match=f"cannot read {missing}"):
        load_config(missing)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("[store", "is not valid TOML"),
        ("", "store"),
        (SQLITE + "\n[unknown]\nkey = 1\n", "unknown"),
        (SQLITE.replace('login = "operator-a"', 'login = " "'), "login"),
        (SQLITE.replace('login = "operator-a"\n', ""), "login"),
        (SQLITE.replace('path = "/var/lib/aura/aura.db"\n', ""), "path"),
        (SQLITE.replace('"/var/lib/aura/aura.db"', '"relative.db"'), "absolute"),
        (SQLITE.replace('"sqlite"', '"mysql"'), "backend"),
        (POSTGRESQL.replace('user = "operator-a"\n', ""), "user"),
        (POSTGRESQL.replace("port = 5432", "port = 0"), "port"),
        (
            POSTGRESQL.replace(
                '"/home/operator/.config/aura/password"', '"password"'
            ),
            "absolute",
        ),
        (POSTGRESQL.replace("= 120", "= 0"), "adapter_timeout_seconds"),
        (POSTGRESQL.replace('["tekton"]', '["shell"]'), "adapters"),
        (POSTGRESQL.replace('["tekton"]', '["tekton", "tekton"]'), "adapters"),
        (POSTGRESQL.replace('"aura@example.com"', '"aura"'), "author_email"),
    ],
    ids=[
        "invalid-toml",
        "empty",
        "unknown-table",
        "blank-login",
        "missing-login",
        "missing-path",
        "relative-path",
        "unknown-backend",
        "missing-user",
        "bad-port",
        "relative-password-file",
        "zero-timeout",
        "unknown-adapter",
        "repeated-adapter",
        "bad-email",
    ],
)
def test_an_invalid_config_is_an_error(tmp_path, text, message) -> None:
    with pytest.raises(ConfigError, match=message):
        load_config(write(tmp_path, text))
