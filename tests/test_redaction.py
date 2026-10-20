from __future__ import annotations

from typing import Any

import pytest

from aura.redaction import RedactionPolicy, redact, redact_parameters


def test_redacts_default_secret_fields_recursively() -> None:
    value = {
        "cluster_name": "example",
        "password": "super-secret",
        "metadata": {
            "Token": "also-secret",
            "region": "us-east-1",
        },
        "items": [
            {
                "name": "cluster-secret",
                "private_key": "ssh-key",
            }
        ],
    }

    result = redact(value)

    assert result.value == {
        "cluster_name": "example",
        "password": "[REDACTED]",
        "metadata": {
            "Token": "[REDACTED]",
            "region": "us-east-1",
        },
        "items": [
            {
                "name": "cluster-secret",
                "private_key": "[REDACTED]",
            }
        ],
    }
    assert result.redacted_fields == [
        "items[0].private_key",
        "metadata.Token",
        "password",
    ]


def test_redaction_does_not_mutate_original_value() -> None:
    value: dict[str, Any] = {"password": "super-secret", "safe": "value"}

    redact(value)

    assert value == {"password": "super-secret", "safe": "value"}


def test_redaction_accepts_custom_secret_fields() -> None:
    policy = RedactionPolicy(secret_fields={"cluster_token"})
    value = {"cluster_token": "secret", "password": "not-targeted"}

    result = policy.redact(value)

    assert result.value == {"cluster_token": "[REDACTED]", "password": "not-targeted"}
    assert result.redacted_fields == ["cluster_token"]


def test_redaction_preserves_non_secret_scalars_and_containers() -> None:
    value = {
        "enabled": True,
        "count": 3,
        "ratio": 0.5,
        "name": None,
        "tags": ["base", "virt"],
    }

    result = redact(value)

    assert result.value == value
    assert result.redacted_fields == []




@pytest.mark.parametrize(
    "field",
    [
        "pullSecret",
        "Client-Secret",
        "aws_secret_access_key",
        "accessToken",
        "ssh-private-key",
    ],
)
def test_redacts_secret_field_spelling_variants(field: str) -> None:
    result = redact({field: "value"})

    assert result.value == {field: "[REDACTED]"}
    assert result.redacted_fields == [field]


@pytest.mark.parametrize("field", ["pullSecretRef", "tokenTTL"])
def test_keeps_fields_that_only_contain_secret_words(field: str) -> None:
    assert redact({field: "value"}).redacted_fields == []



@pytest.mark.parametrize(
    "name",
    ["github-token", "db_password", "AWS-Credentials", "registry-apiKey"],
)
def test_redacts_parameters_with_secret_terms(name: str) -> None:
    result = redact_parameters({name: "value", "cluster-name": "example"})

    assert result.value == {name: "[REDACTED]", "cluster-name": "example"}
    assert result.redacted_fields == [name]


@pytest.mark.parametrize("name", ["keycloak-admin-secret", "spoke-kubeconfig"])
def test_keeps_allowlisted_parameters(name: str) -> None:
    assert redact_parameters({name: "value"}).value == {name: "value"}
