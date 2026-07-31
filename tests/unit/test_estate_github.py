"""Unit tests for the GitHub API helpers in `concordat.estate_github`.

Client construction and the organisation/personal repository-creation paths are
exercised directly; the provisioning flow that drives them is covered by the
`init_estate` suite.
"""

from __future__ import annotations

import typing as typ

import pytest
from github3 import exceptions as github3_exceptions

from concordat import estate, estate_github
from concordat.errors import ConcordatError
from concordat.estate_repository import _build_client

if typ.TYPE_CHECKING:
    import pytest_mock


class TestCreateRepository:
    """Contracts of the organisation/personal repository-creation helpers.

    The helpers live in ``estate_github``; ``_create_repository`` remains the
    orchestration seam that ``estate_repository`` re-exports and tests patch.
    """

    EXPECTED_OPTIONS: typ.ClassVar[dict[str, object]] = {
        "private": True,
        "auto_init": False,
        "description": "Platform standards repository managed by concordat",
    }

    def test_organisation_lookup_auth_failure_is_translated(
        self,
        mocker: pytest_mock.MockFixture,
    ) -> None:
        """A rejected organisation lookup reports the organisation error."""
        client = mocker.Mock()
        client.organization.side_effect = github3_exceptions.AuthenticationFailed(
            mocker.Mock()
        )

        with pytest.raises(estate.GitHubOrganizationAuthenticationError):
            estate_github._find_organization(client, "example")

    def test_missing_organisation_returns_none(
        self,
        mocker: pytest_mock.MockFixture,
    ) -> None:
        """A NotFound organisation selects the personal-owner path."""
        client = mocker.Mock()
        client.organization.side_effect = github3_exceptions.NotFoundError(
            mocker.Mock()
        )

        assert estate_github._find_organization(client, "example") is None, (
            "a missing organisation should resolve to None"
        )

    def test_organisation_creation_auth_failure_is_translated(
        self,
        mocker: pytest_mock.MockFixture,
    ) -> None:
        """A rejected organisation creation reports the creation error."""
        org = mocker.Mock()
        org.create_repository.side_effect = github3_exceptions.AuthenticationFailed(
            mocker.Mock()
        )

        with pytest.raises(estate.GitHubRepositoryCreationAuthenticationError):
            estate_github._create_organization_repository(org, "example", "core")

    def test_organisation_path_never_touches_personal_methods(
        self,
        mocker: pytest_mock.MockFixture,
    ) -> None:
        """An existing organisation short-circuits the personal-owner path."""
        client = mocker.Mock()
        org = mocker.Mock()
        client.organization.return_value = org

        estate_github._create_repository(client, "example", "core")

        org.create_repository.assert_called_once_with("core", **self.EXPECTED_OPTIONS)
        client.me.assert_not_called()
        client.create_repository.assert_not_called()

    @pytest.mark.parametrize(
        "user_login",
        [
            pytest.param(None, id="no-authenticated-user"),
            pytest.param("other", id="mismatched-login"),
        ],
    )
    def test_personal_owner_must_match_authenticated_user(
        self,
        mocker: pytest_mock.MockFixture,
        user_login: str | None,
    ) -> None:
        """Only the authenticated user may own a personal repository."""
        client = mocker.Mock()
        if user_login is None:
            client.me.return_value = None
        else:
            client.me.return_value = mocker.Mock(login=user_login)

        with pytest.raises(estate.RepositoryCreationPermissionError):
            estate_github._create_personal_repository(client, "example", "core")

        client.create_repository.assert_not_called()

    def test_personal_creation_auth_failure_is_translated(
        self,
        mocker: pytest_mock.MockFixture,
    ) -> None:
        """A rejected personal creation reports the repository error."""
        client = mocker.Mock()
        client.me.return_value = mocker.Mock(login="example")
        client.create_repository.side_effect = github3_exceptions.AuthenticationFailed(
            mocker.Mock()
        )

        with pytest.raises(estate.GitHubRepositoryAuthenticationError):
            estate_github._create_personal_repository(client, "example", "core")

    def test_personal_path_creates_with_unchanged_options(
        self,
        mocker: pytest_mock.MockFixture,
    ) -> None:
        """A missing organisation creates the repository for its owner."""
        client = mocker.Mock()
        client.organization.side_effect = github3_exceptions.NotFoundError(
            mocker.Mock()
        )
        client.me.return_value = mocker.Mock(login="example")

        estate_github._create_repository(client, "example", "core")

        client.create_repository.assert_called_once_with(
            "core", **self.EXPECTED_OPTIONS
        )


def test_build_client_requires_token() -> None:
    """Reject GitHub client creation when no token is provided."""
    with pytest.raises(ConcordatError):
        _build_client(None)


def test_build_client_uses_token(mocker: pytest_mock.MockFixture) -> None:
    """Authenticate the GitHub client using the provided token."""
    fake = mocker.Mock()
    # `_build_client` lives in `estate_github`, so its `github3` reference —
    # unlike the function seams — is patched at that implementation module.
    mocked_ctor = mocker.patch.object(
        estate_github.github3, "GitHub", return_value=fake
    )

    client = _build_client("secret")

    assert client is fake
    mocked_ctor.assert_called_once_with(token="secret")  # noqa: S106
