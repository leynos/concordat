"""Unit tests for estate management helpers."""

from __future__ import annotations

import dataclasses
import typing as typ

import pygit2
import pytest
from github3 import exceptions as github3_exceptions

from concordat import estate
from concordat.errors import ConcordatError
from concordat.estate import (
    EstateRecord,
    GitHubOwnerConfirmationAbortedError,
    MissingGitHubOwnerError,
    NonEmptyRepositoryError,
    RemoteProbe,
    RepositoryIdentityError,
    RepositoryInaccessibleError,
    _build_client,
    _resolve_and_confirm_owner,
    get_active_estate,
    init_estate,
    list_enrolled_repositories,
    list_estates,
    register_estate,
    set_active_estate,
)

if typ.TYPE_CHECKING:
    import pathlib
    import unittest.mock as mock

    import pytest_mock


@pytest.fixture
def init_estate_error_setup(
    tmp_path: pathlib.Path,
    mocker: pytest_mock.MockFixture,
) -> tuple[pathlib.Path, pytest_mock.MockFixture, typ.Any]:
    """Provide shared setup for init_estate error-path tests."""
    config_path = tmp_path / "config.yaml"
    mocker.patch.object(
        estate,
        "_probe_remote",
        return_value=RemoteProbe(reachable=False, exists=False, empty=True, error=None),
    )
    mocker.patch.object(estate, "_bootstrap_template")

    fake_client = mocker.Mock()
    fake_client.repository.return_value = None
    mocker.patch.object(estate, "_build_client", return_value=fake_client)
    return config_path, mocker, fake_client


@pytest.fixture
def mock_remote_probe(
    mocker: pytest_mock.MockFixture,
) -> typ.Callable[..., mock.Mock]:
    """Return a factory for mocking estate remote probes."""

    def factory(
        *,
        reachable: bool,
        exists: bool,
        empty: bool,
        error: str | None = None,
    ) -> mock.Mock:
        probe = RemoteProbe(
            reachable=reachable,
            exists=exists,
            empty=empty,
            error=error,
        )
        return mocker.patch.object(estate, "_probe_remote", return_value=probe)

    return factory


@pytest.fixture
def mock_bootstrap(mocker: pytest_mock.MockFixture) -> mock.Mock:
    """Mock template bootstrapping during init_estate tests."""
    return mocker.patch.object(estate, "_bootstrap_template")


@pytest.fixture
def mock_client_factory(
    mocker: pytest_mock.MockFixture,
) -> typ.Callable[..., mock.Mock]:
    """Return a factory for GitHub client mocks."""

    def factory(*, has_repo: bool | None = None) -> mock.Mock:
        client = mocker.Mock()
        if has_repo is None:
            return client
        client.repository.return_value = object() if has_repo else None
        return client

    return factory


def test_register_estate_sets_active(tmp_path: pathlib.Path) -> None:
    """Persisting the first estate also marks it active."""
    config_path = tmp_path / "config.yaml"
    record = EstateRecord(
        alias="core",
        repo_url="git@github.com:org/core.git",
        github_owner="org",
    )
    register_estate(record, config_path=config_path)

    estates = list_estates(config_path=config_path)
    assert estates == [record]

    active = get_active_estate(config_path=config_path)
    assert active == record


def test_set_active_estate_switches_alias(tmp_path: pathlib.Path) -> None:
    """Switching the active estate updates the config file."""
    config_path = tmp_path / "config.yaml"
    first = EstateRecord(
        alias="core",
        repo_url="git@github.com:org/core.git",
        github_owner="org",
    )
    second = EstateRecord(
        alias="sandbox",
        repo_url="git@github.com:org/sandbox.git",
        github_owner="org",
    )
    register_estate(first, config_path=config_path, set_active_if_missing=True)
    register_estate(second, config_path=config_path, set_active_if_missing=False)

    updated = set_active_estate("sandbox", config_path=config_path)
    assert updated == second
    assert get_active_estate(config_path=config_path) == second


def test_list_enrolled_repositories_reads_inventory(tmp_path: pathlib.Path) -> None:
    """Clone an estate repository and render inventory entries."""
    config_path = tmp_path / "config.yaml"
    repo_path = tmp_path / "estate"
    repo = pygit2.init_repository(repo_path, initial_head="main")
    inventory = repo_path / "tofu" / "inventory"
    inventory.mkdir(parents=True)
    yaml_path = inventory / "repositories.yaml"
    yaml_path.write_text(
        "schema_version: 1\nrepositories:\n"
        "  - name: example/one\n"
        "  - name: other/two\n",
        encoding="utf-8",
    )
    index = repo.index
    index.add_all()
    index.write()
    tree = index.write_tree()
    sig = pygit2.Signature("Test User", "test@example.com")
    repo.create_commit("refs/heads/main", sig, sig, "seed", tree, [])

    register_estate(
        EstateRecord(alias="core", repo_url=str(repo_path), github_owner="example"),
        config_path=config_path,
        set_active_if_missing=True,
    )

    urls = list_enrolled_repositories("core", config_path=config_path)
    assert urls == [
        "git@github.com:example/one.git",
        "git@github.com:other/two.git",
    ]


def test_init_estate_creates_repository_when_missing(
    tmp_path: pathlib.Path,
    mocker: pytest_mock.MockFixture,
) -> None:
    """init_estate provisions a repository when the remote is absent."""
    config_path = tmp_path / "config.yaml"
    mocker.patch.object(
        estate,
        "_probe_remote",
        return_value=RemoteProbe(reachable=False, exists=False, empty=True, error=None),
    )
    mocker.patch.object(estate, "_bootstrap_template")

    fake_client = mocker.Mock()
    fake_client.repository.return_value = None
    fake_org = mocker.Mock()
    fake_client.organization.return_value = fake_org
    mocker.patch.object(estate, "_build_client", return_value=fake_client)

    record = init_estate(
        "core",
        "git@github.com:example/core.git",
        github_token="token",  # noqa: S106
        confirm=lambda _: True,
        config_path=config_path,
    )

    assert record.alias == "core"
    assert record.github_owner == "example"
    fake_client.organization.assert_called_once_with("example")
    fake_org.create_repository.assert_called_once()
    stored = list_estates(config_path=config_path)[0]
    assert stored.alias == "core"
    assert stored.github_owner == "example"


def test_init_estate_requires_owner_for_non_github_remote(
    tmp_path: pathlib.Path,
    mocker: pytest_mock.MockFixture,
) -> None:
    """Local remotes require an explicit github_owner override."""
    config_path = tmp_path / "config.yaml"
    mocker.patch.object(
        estate,
        "_probe_remote",
        return_value=RemoteProbe(reachable=True, exists=True, empty=True, error=None),
    )
    mocker.patch.object(estate, "_bootstrap_template")

    with pytest.raises(ConcordatError) as caught:
        init_estate(
            "local",
            str(tmp_path / "estate.git"),
            config_path=config_path,
            confirm=lambda _: True,
        )

    assert "github_owner" in str(caught.value)


def test_init_estate_rejects_empty_owner(
    init_estate_error_setup: tuple[pathlib.Path, pytest_mock.MockFixture, typ.Any],
) -> None:
    """Empty github_owner values are rejected."""
    config_path, mocker, fake_client = init_estate_error_setup
    fake_client.organization.return_value = mocker.Mock()

    with pytest.raises(MissingGitHubOwnerError):
        init_estate(
            "core",
            "git@github.com:example/core.git",
            github_owner="",
            github_token="token",  # noqa: S106
            confirm=lambda _: True,
            config_path=config_path,
        )


def test_init_estate_allows_explicit_owner_override(
    tmp_path: pathlib.Path,
    mocker: pytest_mock.MockFixture,
) -> None:
    """Explicit owners take precedence over repository slugs."""
    config_path = tmp_path / "config.yaml"
    mocker.patch.object(
        estate,
        "_probe_remote",
        return_value=RemoteProbe(reachable=False, exists=False, empty=True, error=None),
    )
    mocker.patch.object(estate, "_bootstrap_template")

    fake_client = mocker.Mock()
    fake_client.repository.return_value = None
    fake_org = mocker.Mock()
    fake_client.organization.return_value = fake_org
    mocker.patch.object(estate, "_build_client", return_value=fake_client)

    record = init_estate(
        "core",
        "git@github.com:example/core.git",
        github_owner="sandbox",
        github_token="token",  # noqa: S106
        confirm=lambda _: True,
        config_path=config_path,
    )

    assert record.github_owner == "sandbox"
    assert list_estates(config_path=config_path)[0].github_owner == "sandbox"


def test_init_estate_prompts_to_confirm_inferred_owner(
    tmp_path: pathlib.Path,
    mocker: pytest_mock.MockFixture,
) -> None:
    """Prompt operators to confirm github_owner inferred from the repo slug."""
    config_path = tmp_path / "config.yaml"
    mocker.patch.object(
        estate,
        "_probe_remote",
        return_value=RemoteProbe(reachable=True, exists=True, empty=True, error=None),
    )
    mocker.patch.object(estate, "_bootstrap_template")

    confirm = mocker.Mock(return_value=True)
    record = init_estate(
        "core",
        "git@github.com:example/platform-standards.git",
        confirm=confirm,
        config_path=config_path,
    )

    assert record.github_owner == "example"
    assert confirm.call_count == 1
    expected_prompt = (
        "Inferred github_owner 'example' from estate repo "
        "'example/platform-standards'. Use this? [y/N]: "
    )
    assert confirm.call_args.args[0] == expected_prompt
    assert list_estates(config_path=config_path)[0].github_owner == "example"


def test_init_estate_aborts_when_inferred_owner_not_confirmed(
    tmp_path: pathlib.Path,
    mocker: pytest_mock.MockFixture,
) -> None:
    """Abort init_estate when the inferred owner is declined."""
    config_path = tmp_path / "config.yaml"
    mocker.patch.object(
        estate,
        "_probe_remote",
        return_value=RemoteProbe(reachable=True, exists=True, empty=True, error=None),
    )
    mocker.patch.object(estate, "_bootstrap_template")

    with pytest.raises(
        GitHubOwnerConfirmationAbortedError,
        match=r"confirmation declined",
    ):
        init_estate(
            "core",
            "git@github.com:example/platform-standards.git",
            confirm=lambda _: False,
            config_path=config_path,
        )


@dataclasses.dataclass
class OwnerResolutionScenario:
    """Test scenario for resolving github_owner."""

    slug: str | None
    github_owner: str | None
    confirm_response: str
    expected_result: str | None
    confirmer_called: bool
    should_raise: type[Exception] | None


@pytest.mark.parametrize(
    "scenario",
    [
        pytest.param(
            OwnerResolutionScenario(
                slug="example/platform-standards",
                github_owner="sandbox",
                confirm_response="yes",
                expected_result="sandbox",
                confirmer_called=False,
                should_raise=None,
            ),
            id="explicit-owner-bypasses-confirmation",
        ),
        pytest.param(
            OwnerResolutionScenario(
                slug="example/platform-standards",
                github_owner=None,
                confirm_response="yes",
                expected_result="example",
                confirmer_called=True,
                should_raise=None,
            ),
            id="inferred-owner-confirmed",
        ),
        pytest.param(
            OwnerResolutionScenario(
                slug="example/platform-standards",
                github_owner=None,
                confirm_response="no",
                expected_result=None,
                confirmer_called=True,
                should_raise=GitHubOwnerConfirmationAbortedError,
            ),
            id="inferred-owner-declined",
        ),
    ],
)
def test_resolve_and_confirm_owner_behavior(
    scenario: OwnerResolutionScenario,
    mocker: pytest_mock.MockFixture,
) -> None:
    """Validate owner resolution with explicit and inferred values.

    Scenarios:
    - Explicit `github_owner` bypasses the confirmer.
    - Inferred owners prompt for confirmation and return the slug owner when
      accepted.
    - Declining the inferred owner raises GitHubOwnerConfirmationAbortedError.
    """
    confirm_bool = scenario.confirm_response == "yes"
    confirmer = mocker.Mock(return_value=confirm_bool)

    if scenario.should_raise is not None:
        with pytest.raises(scenario.should_raise):
            _resolve_and_confirm_owner(scenario.slug, scenario.github_owner, confirmer)
    else:
        resolved = _resolve_and_confirm_owner(
            scenario.slug,
            scenario.github_owner,
            confirmer,
        )
        assert resolved == scenario.expected_result

    if scenario.confirmer_called:
        confirmer.assert_called_once()
    else:
        confirmer.assert_not_called()


def test_init_estate_does_not_prompt_when_owner_is_explicit(
    tmp_path: pathlib.Path,
    mocker: pytest_mock.MockFixture,
) -> None:
    """Explicit github_owner skips the inferred-owner confirmation prompt."""
    config_path = tmp_path / "config.yaml"
    mocker.patch.object(
        estate,
        "_probe_remote",
        return_value=RemoteProbe(reachable=True, exists=True, empty=True, error=None),
    )
    mocker.patch.object(estate, "_bootstrap_template")

    confirm = mocker.Mock(return_value=True)
    record = init_estate(
        "core",
        "git@github.com:example/platform-standards.git",
        github_owner="sandbox",
        confirm=confirm,
        config_path=config_path,
    )

    assert record.github_owner == "sandbox"
    confirm.assert_not_called()


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
    if scenario.match:
        error_context = pytest.raises(scenario.expected_error, match=scenario.match)
    else:
        error_context = pytest.raises(scenario.expected_error)

    with error_context:
        init_estate(
            "core",
            resolved_repo_url,
            github_owner=scenario.github_owner,
            github_token=scenario.github_token,
            confirm=lambda _: True,
            config_path=config_path,
        )
    mock_bootstrap.assert_not_called()


def test_init_estate_raises_when_remote_is_inaccessible(
    tmp_path: pathlib.Path,
    mocker: pytest_mock.MockFixture,
) -> None:
    """Raise RepositoryInaccessibleError when GitHub reports an existing repo."""
    config_path = tmp_path / "config.yaml"
    mocker.patch.object(
        estate,
        "_probe_remote",
        return_value=RemoteProbe(reachable=False, exists=True, empty=True, error=None),
    )
    mocker.patch.object(estate, "_bootstrap_template")

    fake_client = mocker.Mock()
    fake_client.repository.return_value = object()
    mocker.patch.object(estate, "_build_client", return_value=fake_client)

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
) -> None:
    """Create a repo when GitHub reports it missing but SSH is unreachable."""
    config_path = tmp_path / "config.yaml"
    mocker.patch.object(
        estate,
        "_probe_remote",
        return_value=RemoteProbe(reachable=False, exists=True, empty=True, error=None),
    )
    mocker.patch.object(estate, "_bootstrap_template")

    fake_client = mocker.Mock()
    fake_client.repository.return_value = None
    fake_org = mocker.Mock()
    fake_client.organization.return_value = fake_org
    mocker.patch.object(estate, "_build_client", return_value=fake_client)

    create_repo = mocker.patch.object(estate, "_create_repository")
    confirm = mocker.Mock(return_value=True)

    record = init_estate(
        "core",
        "git@github.com:example/platform-standards.git",
        github_token="token",  # noqa: S106
        confirm=confirm,
        config_path=config_path,
    )

    assert record.github_owner == "example"
    create_repo.assert_called_once_with(fake_client, "example", "platform-standards")


def test_init_estate_translates_authentication_errors(
    init_estate_error_setup: tuple[pathlib.Path, pytest_mock.MockFixture, typ.Any],
) -> None:
    """Surface authentication failures when provisioning estates."""
    config_path, mocker, fake_client = init_estate_error_setup
    fake_client.organization.side_effect = github3_exceptions.AuthenticationFailed(
        mocker.Mock()
    )
    mocker.patch.object(estate, "_build_client", return_value=fake_client)

    with pytest.raises(ConcordatError) as caught:
        init_estate(
            "core",
            "git@github.com:example/core.git",
            github_token="token",  # noqa: S106
            confirm=lambda _: True,
            config_path=config_path,
        )

    assert "GitHub authentication failed" in str(caught.value)


def test_build_client_requires_token() -> None:
    """Reject GitHub client creation when no token is provided."""
    with pytest.raises(ConcordatError):
        _build_client(None)


def test_build_client_uses_token(mocker: pytest_mock.MockFixture) -> None:
    """Authenticate the GitHub client using the provided token."""
    fake = mocker.Mock()
    mocked_ctor = mocker.patch.object(estate.github3, "GitHub", return_value=fake)

    client = _build_client("secret")

    assert client is fake
    mocked_ctor.assert_called_once_with(token="secret")  # noqa: S106
