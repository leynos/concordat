"""Unit tests for `init_estate` remote-state and provisioning error paths.

These cover what `init_estate` does when the remote is missing, unreachable,
inaccessible, or non-empty, and how GitHub authentication failures surface. The
successful enrolment flows live in `test_estate_init`.

`mock_remote_probe` and `mock_bootstrap` are shared with `test_estate_init`
and live in `tests/unit/conftest.py`.
"""

from __future__ import annotations

import dataclasses
import typing as typ

import pytest
from github3 import exceptions as github3_exceptions

from concordat import estate_repository
from concordat.errors import ConcordatError
from concordat.estate import (
    MissingGitHubOwnerError,
    NonEmptyRepositoryError,
    RemoteProbe,
    RepositoryIdentityError,
    RepositoryInaccessibleError,
    init_estate,
)

if typ.TYPE_CHECKING:
    import pathlib
    import unittest.mock as mock

    import pytest_mock


@pytest.fixture
def init_estate_error_setup(
    tmp_path: pathlib.Path,
    mocker: pytest_mock.MockFixture,
) -> tuple[pathlib.Path, mock.Mock]:
    """Provide shared setup for init_estate error-path tests."""
    config_path = tmp_path / "config.yaml"
    mocker.patch.object(
        estate_repository,
        "_probe_remote",
        return_value=RemoteProbe(reachable=False, exists=False, empty=True, error=None),
    )
    mocker.patch.object(estate_repository, "_bootstrap_template")

    fake_client = mocker.Mock()
    fake_client.repository.return_value = None
    mocker.patch.object(estate_repository, "_build_client", return_value=fake_client)
    return config_path, fake_client


@dataclasses.dataclass
class InitEstateErrorScenario:
    """Test scenario for init_estate error conditions."""

    probe_state: dict[str, bool]
    repo_url: str
    github_owner: str | None
    github_token: str | None
    expected_error: type[Exception]
    match: str | None = None


@pytest.mark.parametrize(
    "scenario",
    [
        pytest.param(
            InitEstateErrorScenario(
                probe_state={"reachable": False, "exists": False, "empty": True},
                repo_url="git@github.com:example.git",
                github_owner=None,
                github_token="token",  # noqa: S106
                expected_error=RepositoryIdentityError,
            ),
            id="malformed-slug-raises",
        ),
        pytest.param(
            InitEstateErrorScenario(
                probe_state={"reachable": False, "exists": True, "empty": True},
                repo_url="{tmp_path}/estate.git",
                github_owner=None,
                github_token=None,
                expected_error=MissingGitHubOwnerError,
                match=r"github_owner",
            ),
            id="non-github-remote-missing-owner-raises",
        ),
        pytest.param(
            InitEstateErrorScenario(
                probe_state={"reachable": True, "exists": True, "empty": False},
                repo_url="git@github.com:example/platform-standards.git",
                github_owner=None,
                github_token=None,
                expected_error=NonEmptyRepositoryError,
            ),
            id="non-empty-remote-rejected",
        ),
    ],
)
def test_init_estate_error_conditions(
    tmp_path: pathlib.Path,
    scenario: InitEstateErrorScenario,
    mock_remote_probe: typ.Callable[..., mock.Mock],
    mock_bootstrap: mock.Mock,
) -> None:
    """Cover init_estate error paths for remote and slug validation.

    Scenarios:
    - Reject non-empty remotes.
    - Reject malformed GitHub slugs that lack owner/name pairs.
    - Reject missing github_owner for non-GitHub remotes.
    """
    config_path = tmp_path / "config.yaml"
    mock_remote_probe(**scenario.probe_state)

    resolved_repo_url = scenario.repo_url.format(tmp_path=tmp_path)

    with pytest.raises(scenario.expected_error, match=scenario.match):
        init_estate(
            "core",
            resolved_repo_url,
            github_owner=scenario.github_owner,
            github_token=scenario.github_token,
            confirm=lambda _: True,
            config_path=config_path,
        )
    mock_bootstrap.assert_not_called()


def test_init_estate_rejects_non_empty_remote_without_building_a_client(
    tmp_path: pathlib.Path,
    mocker: pytest_mock.MockFixture,
    mock_remote_probe: typ.Callable[..., mock.Mock],
    mock_bootstrap: mock.Mock,
) -> None:
    """A reachable, non-empty remote is rejected before GitHub authentication.

    A token is supplied so the rejection cannot be explained by a missing
    credential: the only proof is that the client is never constructed.
    """
    config_path = tmp_path / "config.yaml"
    mock_remote_probe(reachable=True, exists=True, empty=False)
    build_client = mocker.patch.object(estate_repository, "_build_client")

    with pytest.raises(NonEmptyRepositoryError):
        init_estate(
            "core",
            "git@github.com:example/platform-standards.git",
            github_token="token",  # noqa: S106
            confirm=lambda _: True,
            config_path=config_path,
        )

    build_client.assert_not_called()
    mock_bootstrap.assert_not_called()


def test_init_estate_raises_when_remote_is_inaccessible(
    tmp_path: pathlib.Path,
    mocker: pytest_mock.MockFixture,
    mock_remote_probe: typ.Callable[..., mock.Mock],
    mock_bootstrap: mock.Mock,
) -> None:
    """Raise RepositoryInaccessibleError when GitHub reports an existing repo."""
    config_path = tmp_path / "config.yaml"
    mock_remote_probe(reachable=False, exists=True, empty=True)

    fake_client = mocker.Mock()
    fake_client.repository.return_value = object()
    mocker.patch.object(estate_repository, "_build_client", return_value=fake_client)

    with pytest.raises(RepositoryInaccessibleError):
        init_estate(
            "core",
            "git@github.com:example/platform-standards.git",
            github_token="token",  # noqa: S106
            confirm=lambda _: True,
            config_path=config_path,
        )


def test_init_estate_creates_repository_when_remote_unreachable_and_missing(
    tmp_path: pathlib.Path,
    mocker: pytest_mock.MockFixture,
    mock_remote_probe: typ.Callable[..., mock.Mock],
    mock_bootstrap: mock.Mock,
) -> None:
    """Create a repo when GitHub reports it missing but SSH is unreachable."""
    config_path = tmp_path / "config.yaml"
    mock_remote_probe(reachable=False, exists=True, empty=True)

    fake_client = mocker.Mock()
    fake_client.repository.return_value = None
    fake_org = mocker.Mock()
    fake_client.organization.return_value = fake_org
    mocker.patch.object(estate_repository, "_build_client", return_value=fake_client)

    create_repo = mocker.patch.object(estate_repository, "_create_repository")
    confirm = mocker.Mock(return_value=True)

    record = init_estate(
        "core",
        "git@github.com:example/platform-standards.git",
        github_token="token",  # noqa: S106
        confirm=confirm,
        config_path=config_path,
    )

    assert record.github_owner == "example", record
    create_repo.assert_called_once_with(fake_client, "example", "platform-standards")


def test_init_estate_translates_authentication_errors(
    init_estate_error_setup: tuple[pathlib.Path, mock.Mock],
    mocker: pytest_mock.MockFixture,
) -> None:
    """Surface authentication failures when provisioning estates."""
    config_path, fake_client = init_estate_error_setup
    fake_client.organization.side_effect = github3_exceptions.AuthenticationFailed(
        mocker.Mock()
    )

    with pytest.raises(ConcordatError) as caught:
        init_estate(
            "core",
            "git@github.com:example/core.git",
            github_token="token",  # noqa: S106
            confirm=lambda _: True,
            config_path=config_path,
        )

    assert "GitHub authentication failed" in str(caught.value), caught.value
