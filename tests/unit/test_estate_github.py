"""Unit tests for the GitHub API helpers in `concordat.estate_github`.

Client construction and the organisation/personal repository-creation paths are
exercised directly; the provisioning flow that drives them is covered by the
`init_estate` suite.
"""

from __future__ import annotations

import dataclasses
import typing as typ

import pytest
from github3 import exceptions as github3_exceptions

from concordat import estate, estate_github
from concordat.errors import ConcordatError
from concordat.estate_repository import _build_client

if typ.TYPE_CHECKING:
    import github3
    import github3.orgs
    import pytest_mock


# github3 raises 401 as `AuthenticationFailed` and 403 as `ForbiddenError`.
# They are siblings under `ResponseError`, so each boundary must translate
# both; the production `_REJECTED` tuple names them together.
type Rejection = type[github3_exceptions.ResponseError]


def _client_rejecting_organisation_lookup(
    mocker: pytest_mock.MockFixture,
    rejection: Rejection,
) -> github3.GitHub:
    """Return a client whose organisation lookup is rejected."""
    client = mocker.Mock()
    client.organization.side_effect = rejection(mocker.Mock())
    return client


def _look_up_organisation(client: github3.GitHub) -> None:
    """Resolve the organisation that would own the estate repository."""
    estate_github._find_organization(client, "example")


def _organisation_rejecting_creation(
    mocker: pytest_mock.MockFixture,
    rejection: Rejection,
) -> github3.orgs.Organization:
    """Return an organisation that refuses to create a repository."""
    org = mocker.Mock()
    org.create_repository.side_effect = rejection(mocker.Mock())
    return org


def _create_organisation_repository(org: github3.orgs.Organization) -> None:
    """Create the estate repository inside an organisation."""
    estate_github._create_organization_repository(org, "example", "core")


def _client_rejecting_personal_creation(
    mocker: pytest_mock.MockFixture,
    rejection: Rejection,
) -> github3.GitHub:
    """Return an authenticated client that refuses to create a repository.

    ``me()`` must still identify the expected owner, since the permission
    check precedes the creation call this scenario is about.

    Returns
    -------
    github3.GitHub
        An authenticated client configured to reject repository creation.
    """
    client = mocker.Mock()
    client.me.return_value = mocker.Mock(login="example")
    client.create_repository.side_effect = rejection(mocker.Mock())
    return client


def _create_personal_repository(client: github3.GitHub) -> None:
    """Create the estate repository for the authenticated user."""
    estate_github._create_personal_repository(client, "example", "core")


@dataclasses.dataclass(frozen=True)
class AuthenticationFailureScenario:
    """Describe one GitHub API authentication-translation contract."""

    # The subject type is erased rather than `object`: parameter types are
    # contravariant, so a `Callable[[object], None]` field would reject the
    # boundary-typed helpers below. Each setup/invoke pair is matched at
    # construction, so the erasure is contained to this table.
    setup: typ.Callable[[pytest_mock.MockFixture, Rejection], typ.Any]
    invoke: typ.Callable[[typ.Any], None]
    error_type: type[ConcordatError]


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

    @pytest.mark.parametrize(
        "rejection",
        [
            pytest.param(
                github3_exceptions.AuthenticationFailed,
                id="unauthenticated",
            ),
            pytest.param(github3_exceptions.ForbiddenError, id="forbidden"),
        ],
    )
    @pytest.mark.parametrize(
        "scenario",
        [
            pytest.param(
                AuthenticationFailureScenario(
                    setup=_client_rejecting_organisation_lookup,
                    invoke=_look_up_organisation,
                    error_type=estate.GitHubOrganizationAuthenticationError,
                ),
                id="organisation-lookup",
            ),
            pytest.param(
                AuthenticationFailureScenario(
                    setup=_organisation_rejecting_creation,
                    invoke=_create_organisation_repository,
                    error_type=estate.GitHubRepositoryCreationAuthenticationError,
                ),
                id="organisation-creation",
            ),
            pytest.param(
                AuthenticationFailureScenario(
                    setup=_client_rejecting_personal_creation,
                    invoke=_create_personal_repository,
                    error_type=estate.GitHubRepositoryAuthenticationError,
                ),
                id="personal-creation",
            ),
        ],
    )
    def test_rejected_call_is_translated(
        self,
        mocker: pytest_mock.MockFixture,
        scenario: AuthenticationFailureScenario,
        rejection: Rejection,
    ) -> None:
        """Each rejected call reports the error naming its own boundary.

        The boundaries share a rejection but not a diagnosis: a refused lookup
        says nothing about whether the owner is an organisation, so each keeps
        a distinct error class. Both a 401 and a 403 must translate, since
        github3 models them as unrelated siblings.
        """
        subject = scenario.setup(mocker, rejection)

        with pytest.raises(scenario.error_type):
            scenario.invoke(subject)

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


@pytest.mark.parametrize(
    "rejection",
    [
        pytest.param(github3_exceptions.AuthenticationFailed, id="unauthenticated"),
        pytest.param(github3_exceptions.ForbiddenError, id="forbidden"),
    ],
)
def test_rejected_me_lookup_is_translated(
    mocker: pytest_mock.MockFixture,
    rejection: Rejection,
) -> None:
    """A rejected `me()` becomes an estate error rather than escaping raw.

    Identifying the authenticated user is a credentialed call, so it needs the
    same translation as creating the repository.
    """
    client = mocker.Mock()
    client.me.side_effect = rejection(mocker.Mock())

    with pytest.raises(estate.GitHubAuthenticationError):
        estate_github._create_personal_repository(client, "example", "core")

    client.create_repository.assert_not_called()
