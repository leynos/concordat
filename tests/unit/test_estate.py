"""Unit tests for estate management helpers."""

from __future__ import annotations

import pathlib

import pygit2
import pytest
import pytest_mock

from concordat import estate
from concordat.errors import ConcordatError
from concordat.estate import (
    DEFAULT_BRANCH,
    DEFAULT_INVENTORY_PATH,
    EstateRecord,
    RemoteProbe,
    _build_client,
    get_active_estate,
    init_estate,
    list_enrolled_repositories,
    list_estates,
    register_estate,
    set_active_estate,
)


def test_register_estate_sets_active(tmp_path: pathlib.Path) -> None:
    """Persisting the first estate also marks it active."""
    config_path = tmp_path / "config.yaml"
    record = EstateRecord(alias="core", repo_url="git@github.com:org/core.git")
    register_estate(record, config_path=config_path)

    estates = list_estates(config_path=config_path)
    assert estates == [record]

    active = get_active_estate(config_path=config_path)
    assert active == record


def test_set_active_estate_switches_alias(tmp_path: pathlib.Path) -> None:
    """Switching the active estate updates the config file."""
    config_path = tmp_path / "config.yaml"
    first = EstateRecord(alias="core", repo_url="git@github.com:org/core.git")
    second = EstateRecord(alias="sandbox", repo_url="git@github.com:org/sandbox.git")
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
        EstateRecord(alias="core", repo_url=str(repo_path)),
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
        github_token="token",
        confirm=lambda _: True,
        config_path=config_path,
    )

    assert record.alias == "core"
    fake_client.organization.assert_called_once_with("example")
    fake_org.create_repository.assert_called_once()
    assert list_estates(config_path=config_path)[0].alias == "core"


def test_build_client_requires_token() -> None:
    with pytest.raises(ConcordatError):
        _build_client(None)


def test_build_client_uses_token(mocker: pytest_mock.MockFixture) -> None:
    fake = mocker.Mock()
    mocked_ctor = mocker.patch.object(estate.github3, "GitHub", return_value=fake)

    client = _build_client("secret")

    assert client is fake
    mocked_ctor.assert_called_once_with(token="secret")
