"""Unit tests for S3 credential sourcing used by persistence validation."""

from __future__ import annotations

import dataclasses
import typing as typ

import pytest

import concordat.persistence.validation as persistence_validation
from concordat import xdg

REGION: typ.Final = "fr-par"
ENDPOINT: typ.Final = "https://s3.fr-par.scw.cloud"


@dataclasses.dataclass(frozen=True)
class CredentialsCase:
    """One environment credential mapping and expected boto3 values.

    `session_token` defaults to absent so the vendor-mapping cases, which say
    nothing about session tokens, do not have to mention it.
    """

    env: dict[str, str]
    access_key: str
    secret_key: str
    session_token: str | None = None


@pytest.fixture
def captured_client_kwargs(monkeypatch: pytest.MonkeyPatch) -> dict[str, typ.Any]:
    """Clear every backend credential variable and capture the boto3 call.

    The cleanup matters as much as the capture: these tests assert on what the
    factory read from the environment, so a variable inherited from the
    developer's shell would otherwise decide the result.
    """
    for variable in (
        *persistence_validation.AWS_BACKEND_ENV,
        *persistence_validation.SCW_BACKEND_ENV,
        *persistence_validation.SPACES_BACKEND_ENV,
        persistence_validation.AWS_SESSION_TOKEN_VAR,
    ):
        monkeypatch.delenv(variable, raising=False)

    captured: dict[str, typ.Any] = {}

    def fake_client(service_name: str, **kwargs: object) -> object:
        captured["service_name"] = service_name
        captured["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(persistence_validation.boto3, "client", fake_client)
    return captured


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(
            CredentialsCase(
                env={
                    "AWS_ACCESS_KEY_ID": "aws-access",
                    "AWS_SECRET_ACCESS_KEY": "aws-secret",
                },
                access_key="aws-access",
                secret_key="aws-secret",  # noqa: S106
            ),
            id="aws_credentials",
        ),
        pytest.param(
            CredentialsCase(
                env={"SCW_ACCESS_KEY": "scw-access", "SCW_SECRET_KEY": "scw-secret"},
                access_key="scw-access",
                secret_key="scw-secret",  # noqa: S106
            ),
            id="scw_credentials",
        ),
        pytest.param(
            CredentialsCase(
                env={
                    "SPACES_ACCESS_KEY_ID": "spaces-access",
                    "SPACES_SECRET_ACCESS_KEY": "spaces-secret",
                },
                access_key="spaces-access",
                secret_key="spaces-secret",  # noqa: S106
            ),
            id="spaces_credentials",
        ),
    ],
)
def test_default_s3_client_factory_maps_environment_credentials(
    monkeypatch: pytest.MonkeyPatch,
    captured_client_kwargs: dict[str, typ.Any],
    case: CredentialsCase,
) -> None:
    """The default S3 client factory supports AWS, SCW, and Spaces env vars."""
    for key, value in case.env.items():
        monkeypatch.setenv(key, value)

    persistence_validation._default_s3_client_factory(REGION, ENDPOINT)

    assert captured_client_kwargs["service_name"] == "s3"
    kwargs = typ.cast("dict[str, object]", captured_client_kwargs["kwargs"])
    assert kwargs["aws_access_key_id"] == case.access_key
    assert kwargs["aws_secret_access_key"] == case.secret_key


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(
            CredentialsCase(
                env={
                    "AWS_ACCESS_KEY_ID": "aws-access",
                    "AWS_SECRET_ACCESS_KEY": "aws-secret",
                    "SCW_ACCESS_KEY": "scw-access",
                    "SCW_SECRET_KEY": "scw-secret",
                },
                access_key="aws-access",
                secret_key="aws-secret",  # noqa: S106
            ),
            id="aws-precedes-scw",
        ),
        pytest.param(
            CredentialsCase(
                env={
                    "SCW_ACCESS_KEY": "scw-access",
                    "SCW_SECRET_KEY": "scw-secret",
                    "AWS_SESSION_TOKEN": "   ",
                },
                access_key="scw-access",
                secret_key="scw-secret",  # noqa: S106
                # Stated rather than defaulted: a token *is* present in the
                # environment here, and the expectation is that it is dropped.
                session_token=None,
            ),
            id="blank-session-token-is-omitted",
        ),
        pytest.param(
            CredentialsCase(
                env={
                    "SCW_ACCESS_KEY": "scw-access",
                    "SCW_SECRET_KEY": "scw-secret",
                    "AWS_SESSION_TOKEN": "session-token",
                },
                access_key="scw-access",
                secret_key="scw-secret",  # noqa: S106
                session_token="session-token",  # noqa: S106
            ),
            id="session-token-is-forwarded",
        ),
    ],
)
def test_default_s3_client_factory_applies_backend_and_session_precedence(
    monkeypatch: pytest.MonkeyPatch,
    captured_client_kwargs: dict[str, typ.Any],
    case: CredentialsCase,
) -> None:
    """Which backend wins, and whether a session token is forwarded.

    Both rules are decided by the same pass over the environment, so they are
    stated together: the AWS/SCW precedence chooses the key pair, and the
    session token is carried only when it survives stripping.
    """
    for key, value in case.env.items():
        monkeypatch.setenv(key, value)

    persistence_validation._default_s3_client_factory(REGION, ENDPOINT)

    kwargs = typ.cast("dict[str, object]", captured_client_kwargs["kwargs"])
    assert kwargs["aws_access_key_id"] == case.access_key, kwargs
    assert kwargs["aws_secret_access_key"] == case.secret_key, kwargs
    if case.session_token is None:
        assert "aws_session_token" not in kwargs, kwargs
    else:
        assert kwargs["aws_session_token"] == case.session_token, kwargs


def test_default_s3_client_factory_leaves_credentials_unset_when_missing(
    captured_client_kwargs: dict[str, typ.Any],
) -> None:
    """When no supported env vars exist, the factory defers to boto3 discovery."""
    persistence_validation._default_s3_client_factory(REGION, ENDPOINT)

    kwargs = typ.cast("dict[str, object]", captured_client_kwargs["kwargs"])
    assert "aws_access_key_id" not in kwargs
    assert "aws_secret_access_key" not in kwargs
    assert "aws_session_token" not in kwargs


def _write_owner_keys(owner: str, access: str, secret: str) -> None:
    """Write *owner*'s S3 credentials file with the mode the loader demands."""
    path = xdg.owner_credentials_path(owner)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"AWS_ACCESS_KEY_ID: {access}\nAWS_SECRET_ACCESS_KEY: {secret}\n",
        encoding="utf-8",
    )
    path.chmod(0o600)


class TestOwnerScopedS3Credentials:
    """The default factory reads the named owner's credentials file."""

    @pytest.fixture
    def captured_kwargs(
        self,
        monkeypatch: pytest.MonkeyPatch,
        xdg_env: dict[str, str],
    ) -> dict[str, typ.Any]:
        """Stub boto3 and return the mapping the client kwargs land in."""
        captured: dict[str, typ.Any] = {}

        def fake_client(service_name: str, **kwargs: object) -> object:
            captured["kwargs"] = kwargs
            return object()

        monkeypatch.setattr(persistence_validation.boto3, "client", fake_client)
        for variable in (
            *persistence_validation.AWS_BACKEND_ENV,
            *persistence_validation.SCW_BACKEND_ENV,
            *persistence_validation.SPACES_BACKEND_ENV,
            persistence_validation.AWS_SESSION_TOKEN_VAR,
        ):
            monkeypatch.delenv(variable, raising=False)
        _write_owner_keys("alpha", "alpha-access", "alpha-secret")
        _write_owner_keys("bravo", "bravo-access", "bravo-secret")
        xdg.set_active_owner("alpha")
        return captured

    def test_named_owner_overrides_the_active_owner(
        self,
        captured_kwargs: dict[str, typ.Any],
    ) -> None:
        """`owner=` selects the credentials file, even against an active owner.

        ``alpha`` is active, so without the parameter the factory would map
        ``alpha``'s keys onto an estate belonging to ``bravo``.
        """
        persistence_validation._default_s3_client_factory(
            "fr-par",
            "https://s3.fr-par.scw.cloud",
            owner="bravo",
        )

        kwargs = typ.cast("dict[str, object]", captured_kwargs["kwargs"])
        assert kwargs["aws_access_key_id"] == "bravo-access", kwargs
        assert kwargs["aws_secret_access_key"] == "bravo-secret", kwargs  # noqa: S105

    def test_without_an_owner_the_active_one_still_applies(
        self,
        captured_kwargs: dict[str, typ.Any],
    ) -> None:
        """Omitting `owner` keeps the previous active-owner behaviour."""
        persistence_validation._default_s3_client_factory(
            "fr-par",
            "https://s3.fr-par.scw.cloud",
        )

        kwargs = typ.cast("dict[str, object]", captured_kwargs["kwargs"])
        assert kwargs["aws_access_key_id"] == "alpha-access", kwargs

    def test_the_environment_still_outranks_the_owner_file(
        self,
        monkeypatch: pytest.MonkeyPatch,
        captured_kwargs: dict[str, typ.Any],
    ) -> None:
        """Owner scoping does not disturb environment-over-file precedence."""
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "env-access")
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "env-secret")

        persistence_validation._default_s3_client_factory(
            "fr-par",
            "https://s3.fr-par.scw.cloud",
            owner="bravo",
        )

        kwargs = typ.cast("dict[str, object]", captured_kwargs["kwargs"])
        assert kwargs["aws_access_key_id"] == "env-access", kwargs
        assert kwargs["aws_secret_access_key"] == "env-secret", kwargs  # noqa: S105
