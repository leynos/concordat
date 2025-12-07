"""Unit tests for backend environment resolution helpers."""

from __future__ import annotations

import dataclasses
import os
import typing as typ

import pytest

from concordat.estate_execution import (
    ALL_BACKEND_ENV_VARS,
    AWS_SESSION_TOKEN_VAR,
    EstateExecutionError,
    _resolve_backend_environment,
)


@dataclasses.dataclass
class BackendEnvTestCase:
    """Test case for backend environment sourcing."""

    env_setup: dict[str, str]
    options_environment: dict[str, str] | None
    expected_access: str
    expected_secret: str


@dataclasses.dataclass
class SessionTokenTestCase:
    """Test case for session token propagation across backends."""

    credentials: dict[str, str]
    session_token: str | None
    expected_access: str
    expected_secret: str
    expect_token_in_result: bool


@pytest.mark.parametrize(
    ("env_setup", "expected_overrides", "test_id"),
    [
        pytest.param(
            {
                "set": {
                    "AWS_ACCESS_KEY_ID": "aws-access",
                    "AWS_SECRET_ACCESS_KEY": "aws-secret",
                    "SCW_ACCESS_KEY": "scw-access",
                    "SCW_SECRET_KEY": "scw-secret",
                    "SPACES_ACCESS_KEY_ID": "spaces-access",
                    "SPACES_SECRET_ACCESS_KEY": "spaces-secret",
                },
                "delete": [],
            },
            {
                "AWS_ACCESS_KEY_ID": "aws-access",
                "AWS_SECRET_ACCESS_KEY": "aws-secret",
            },
            "prefers_aws",
        ),
        pytest.param(
            {
                "set": {
                    "SCW_ACCESS_KEY": "scw-access",
                    "SCW_SECRET_KEY": "scw-secret",
                    "SPACES_ACCESS_KEY_ID": "spaces-access",
                    "SPACES_SECRET_ACCESS_KEY": "spaces-secret",
                },
                "delete": [
                    "AWS_ACCESS_KEY_ID",
                    "AWS_SECRET_ACCESS_KEY",
                ],
            },
            {
                "AWS_ACCESS_KEY_ID": "scw-access",
                "AWS_SECRET_ACCESS_KEY": "scw-secret",
            },
            "scw_over_spaces",
        ),
    ],
)
def test_resolve_backend_environment_precedence(
    monkeypatch: pytest.MonkeyPatch,
    env_setup: dict[str, dict[str, str] | list[str]],
    expected_overrides: dict[str, str],
    test_id: str,
) -> None:
    """Backend environment resolution follows AWS > SCW > SPACES precedence."""
    set_values = typ.cast("dict[str, str]", env_setup["set"])
    for key, value in set_values.items():
        monkeypatch.setenv(key, value)
    delete_keys = typ.cast("list[str]", env_setup["delete"])
    for key in delete_keys:
        monkeypatch.delenv(key, raising=False)

    resolved = _resolve_backend_environment(os.environ)

    assert resolved == expected_overrides


@pytest.mark.parametrize(
    "test_case",
    [
        pytest.param(
            SessionTokenTestCase(
                credentials={
                    "SCW_ACCESS_KEY": "scw-access",
                    "SCW_SECRET_KEY": "scw-secret",
                },
                session_token="sts-session-token",  # noqa: S106
                expected_access="scw-access",
                expected_secret="scw-secret",  # noqa: S106
                expect_token_in_result=True,
            ),
            id="scw_with_token",
        ),
        pytest.param(
            SessionTokenTestCase(
                credentials={
                    "SPACES_ACCESS_KEY_ID": "spaces-access",
                    "SPACES_SECRET_ACCESS_KEY": "spaces-secret",
                },
                session_token="sts-session-token",  # noqa: S106
                expected_access="spaces-access",
                expected_secret="spaces-secret",  # noqa: S106
                expect_token_in_result=True,
            ),
            id="spaces_with_token",
        ),
        pytest.param(
            SessionTokenTestCase(
                credentials={
                    "AWS_ACCESS_KEY_ID": "aws-access",
                    "AWS_SECRET_ACCESS_KEY": "aws-secret",
                },
                session_token="sts-session-token",  # noqa: S106
                expected_access="aws-access",
                expected_secret="aws-secret",  # noqa: S106
                expect_token_in_result=True,
            ),
            id="aws_with_token",
        ),
        pytest.param(
            SessionTokenTestCase(
                credentials={
                    "AWS_ACCESS_KEY_ID": "aws-access",
                    "AWS_SECRET_ACCESS_KEY": "aws-secret",
                },
                session_token="   ",  # noqa: S106
                expected_access="aws-access",
                expected_secret="aws-secret",  # noqa: S106
                expect_token_in_result=False,
            ),
            id="blank_token_omitted",
        ),
    ],
)
def test_resolve_backend_environment_session_token_handling(
    monkeypatch: pytest.MonkeyPatch,
    test_case: SessionTokenTestCase,
) -> None:
    """Session token handling stays consistent across backends."""
    for variable in ALL_BACKEND_ENV_VARS:
        monkeypatch.delenv(variable, raising=False)

    for key, value in test_case.credentials.items():
        monkeypatch.setenv(key, value)
    if test_case.session_token is not None:
        monkeypatch.setenv(AWS_SESSION_TOKEN_VAR, test_case.session_token)

    resolved = _resolve_backend_environment(os.environ)

    assert resolved["AWS_ACCESS_KEY_ID"] == test_case.expected_access
    assert resolved["AWS_SECRET_ACCESS_KEY"] == test_case.expected_secret
    if test_case.expect_token_in_result:
        assert test_case.session_token is not None
        assert resolved[AWS_SESSION_TOKEN_VAR] == test_case.session_token.strip()
    else:
        assert AWS_SESSION_TOKEN_VAR not in resolved


def test_resolve_backend_environment_ignores_blank_scw_and_uses_spaces(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Blank SCW_* values fall back to SPACES_* credentials."""
    for variable in ALL_BACKEND_ENV_VARS:
        monkeypatch.delenv(variable, raising=False)

    monkeypatch.setenv("SCW_ACCESS_KEY", "   ")
    monkeypatch.setenv("SCW_SECRET_KEY", "")
    monkeypatch.setenv("SPACES_ACCESS_KEY_ID", "spaces-access")
    monkeypatch.setenv("SPACES_SECRET_ACCESS_KEY", "spaces-secret")

    resolved = _resolve_backend_environment(os.environ)

    assert resolved == {
        "AWS_ACCESS_KEY_ID": "spaces-access",
        "AWS_SECRET_ACCESS_KEY": "spaces-secret",
    }


def test_resolve_backend_environment_raises_when_all_aliases_blank(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Whitespace aliases without AWS_* raise an execution error."""
    for variable in ALL_BACKEND_ENV_VARS:
        monkeypatch.delenv(variable, raising=False)

    monkeypatch.setenv("SCW_ACCESS_KEY", "   ")
    monkeypatch.setenv("SCW_SECRET_KEY", "   ")
    monkeypatch.setenv("SPACES_ACCESS_KEY_ID", "")
    monkeypatch.setenv("SPACES_SECRET_ACCESS_KEY", "  ")

    with pytest.raises(EstateExecutionError):
        _resolve_backend_environment(os.environ)
