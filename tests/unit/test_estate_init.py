"""Unit tests for the public `init_estate` / enrolment façade flow.

These cover the successful enrolment paths and owner-confirmation behaviour.
Remote-state and provisioning error paths live in `test_estate_provisioning`.
"""

from __future__ import annotations

import typing as typ

import pygit2
import pytest

from concordat import estate_repository
from concordat.errors import ConcordatError
from concordat.estate import (
    EstateRecord,
    GitHubOwnerConfirmationAbortedError,
    MissingGitHubOwnerError,
    RemoteProbe,
    init_estate,
    list_enrolled_repositories,
    list_estates,
    register_estate,
)

if typ.TYPE_CHECKING:
    import pathlib

    import pytest_mock


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
        estate_repository,
        "_probe_remote",
        return_value=RemoteProbe(reachable=False, exists=False, empty=True, error=None),
    )
    mocker.patch.object(estate_repository, "_bootstrap_template")

    fake_client = mocker.Mock()
    fake_client.repository.return_value = None
    fake_org = mocker.Mock()
    fake_client.organization.return_value = fake_org
    mocker.patch.object(estate_repository, "_build_client", return_value=fake_client)

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
        estate_repository,
        "_probe_remote",
        return_value=RemoteProbe(reachable=True, exists=True, empty=True, error=None),
    )
    mocker.patch.object(estate_repository, "_bootstrap_template")

    with pytest.raises(ConcordatError) as caught:
        init_estate(
            "local",
            str(tmp_path / "estate.git"),
            config_path=config_path,
            confirm=lambda _: True,
        )

    assert "github_owner" in str(caught.value)


def test_init_estate_rejects_empty_owner(
    tmp_path: pathlib.Path,
    mocker: pytest_mock.MockFixture,
) -> None:
    """Empty github_owner values are rejected."""
    config_path = tmp_path / "config.yaml"
    mocker.patch.object(
        estate_repository,
        "_probe_remote",
        return_value=RemoteProbe(reachable=False, exists=False, empty=True, error=None),
    )
    mocker.patch.object(estate_repository, "_bootstrap_template")

    fake_client = mocker.Mock()
    fake_client.repository.return_value = None
    fake_client.organization.return_value = mocker.Mock()
    mocker.patch.object(estate_repository, "_build_client", return_value=fake_client)

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
        estate_repository,
        "_probe_remote",
        return_value=RemoteProbe(reachable=False, exists=False, empty=True, error=None),
    )
    mocker.patch.object(estate_repository, "_bootstrap_template")

    fake_client = mocker.Mock()
    fake_client.repository.return_value = None
    fake_org = mocker.Mock()
    fake_client.organization.return_value = fake_org
    mocker.patch.object(estate_repository, "_build_client", return_value=fake_client)

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
        estate_repository,
        "_probe_remote",
        return_value=RemoteProbe(reachable=True, exists=True, empty=True, error=None),
    )
    mocker.patch.object(estate_repository, "_bootstrap_template")

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
        estate_repository,
        "_probe_remote",
        return_value=RemoteProbe(reachable=True, exists=True, empty=True, error=None),
    )
    mocker.patch.object(estate_repository, "_bootstrap_template")

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


def test_init_estate_does_not_prompt_when_owner_is_explicit(
    tmp_path: pathlib.Path,
    mocker: pytest_mock.MockFixture,
) -> None:
    """Explicit github_owner skips the inferred-owner confirmation prompt."""
    config_path = tmp_path / "config.yaml"
    mocker.patch.object(
        estate_repository,
        "_probe_remote",
        return_value=RemoteProbe(reachable=True, exists=True, empty=True, error=None),
    )
    mocker.patch.object(estate_repository, "_bootstrap_template")

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
