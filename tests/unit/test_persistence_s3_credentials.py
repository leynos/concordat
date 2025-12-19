"""Unit tests for S3 credential sourcing used by persistence validation."""

from __future__ import annotations

import typing as typ

import pytest

import concordat.persistence.validation as persistence_validation


@pytest.mark.parametrize(
    ("env", "expected_access", "expected_secret"),
    [
        pytest.param(
            {"AWS_ACCESS_KEY_ID": "aws-access", "AWS_SECRET_ACCESS_KEY": "aws-secret"},
            "aws-access",
            "aws-secret",
            id="aws_credentials",
        ),
        pytest.param(
            {"SCW_ACCESS_KEY": "scw-access", "SCW_SECRET_KEY": "scw-secret"},
            "scw-access",
            "scw-secret",
            id="scw_credentials",
        ),
        pytest.param(
            {
                "SPACES_ACCESS_KEY_ID": "spaces-access",
                "SPACES_SECRET_ACCESS_KEY": "spaces-secret",
            },
            "spaces-access",
            "spaces-secret",
            id="spaces_credentials",
        ),
    ],
)
def test_default_s3_client_factory_maps_environment_credentials(
    monkeypatch: pytest.MonkeyPatch,
    env: dict[str, str],
    expected_access: str,
    expected_secret: str,
) -> None:
    """The default S3 client factory supports AWS, SCW, and Spaces env vars."""
    for variable in (
        *persistence_validation.AWS_BACKEND_ENV,
        *persistence_validation.SCW_BACKEND_ENV,
        *persistence_validation.SPACES_BACKEND_ENV,
        persistence_validation.AWS_SESSION_TOKEN_VAR,
    ):
        monkeypatch.delenv(variable, raising=False)

    for key, value in env.items():
        monkeypatch.setenv(key, value)

    captured: dict[str, typ.Any] = {}

    def fake_client(service_name: str, **kwargs: object) -> object:
        captured["service_name"] = service_name
        captured["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(persistence_validation.boto3, "client", fake_client)

    persistence_validation._default_s3_client_factory(
        "fr-par",
        "https://s3.fr-par.scw.cloud",
    )

    assert captured["service_name"] == "s3"
    kwargs = typ.cast("dict[str, object]", captured["kwargs"])
    assert kwargs["aws_access_key_id"] == expected_access
    assert kwargs["aws_secret_access_key"] == expected_secret


def test_default_s3_client_factory_prefers_aws_over_scw(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When both are present, AWS_* takes precedence over SCW_*."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "aws-access")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "aws-secret")
    monkeypatch.setenv("SCW_ACCESS_KEY", "scw-access")
    monkeypatch.setenv("SCW_SECRET_KEY", "scw-secret")

    captured: dict[str, typ.Any] = {}

    def fake_client(service_name: str, **kwargs: object) -> object:
        captured["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(persistence_validation.boto3, "client", fake_client)

    persistence_validation._default_s3_client_factory(
        "fr-par",
        "https://s3.fr-par.scw.cloud",
    )

    kwargs = typ.cast("dict[str, object]", captured["kwargs"])
    assert kwargs["aws_access_key_id"] == "aws-access"
    assert kwargs["aws_secret_access_key"] == "aws-secret"  # noqa: S105


def test_default_s3_client_factory_omits_session_token_when_blank(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Blank AWS_SESSION_TOKEN should not be forwarded to boto3."""
    monkeypatch.setenv("SCW_ACCESS_KEY", "scw-access")
    monkeypatch.setenv("SCW_SECRET_KEY", "scw-secret")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "   ")

    captured: dict[str, typ.Any] = {}

    def fake_client(service_name: str, **kwargs: object) -> object:
        captured["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(persistence_validation.boto3, "client", fake_client)

    persistence_validation._default_s3_client_factory(
        "fr-par",
        "https://s3.fr-par.scw.cloud",
    )

    kwargs = typ.cast("dict[str, object]", captured["kwargs"])
    assert "aws_session_token" not in kwargs


def test_default_s3_client_factory_forwards_session_token_when_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-blank AWS_SESSION_TOKEN should be forwarded to boto3."""
    monkeypatch.setenv("SCW_ACCESS_KEY", "scw-access")
    monkeypatch.setenv("SCW_SECRET_KEY", "scw-secret")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "session-token")

    captured: dict[str, typ.Any] = {}

    def fake_client(service_name: str, **kwargs: object) -> object:
        captured["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(persistence_validation.boto3, "client", fake_client)

    persistence_validation._default_s3_client_factory(
        "fr-par",
        "https://s3.fr-par.scw.cloud",
    )

    kwargs = typ.cast("dict[str, object]", captured["kwargs"])
    assert kwargs["aws_session_token"] == "session-token"  # noqa: S105


def test_default_s3_client_factory_leaves_credentials_unset_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When no supported env vars exist, the factory defers to boto3 discovery."""
    for variable in (
        *persistence_validation.AWS_BACKEND_ENV,
        *persistence_validation.SCW_BACKEND_ENV,
        *persistence_validation.SPACES_BACKEND_ENV,
        persistence_validation.AWS_SESSION_TOKEN_VAR,
    ):
        monkeypatch.delenv(variable, raising=False)

    captured: dict[str, typ.Any] = {}

    def fake_client(service_name: str, **kwargs: object) -> object:
        captured["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(persistence_validation.boto3, "client", fake_client)

    persistence_validation._default_s3_client_factory(
        "fr-par",
        "https://s3.fr-par.scw.cloud",
    )

    kwargs = typ.cast("dict[str, object]", captured["kwargs"])
    assert "aws_access_key_id" not in kwargs
    assert "aws_secret_access_key" not in kwargs
    assert "aws_session_token" not in kwargs
