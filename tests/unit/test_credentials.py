"""Unit tests for owner-scoped credential resolution."""

from __future__ import annotations

import typing as typ

import pytest

from concordat import credentials, xdg

if typ.TYPE_CHECKING:
    import pathlib

ENV_TOKEN = "ghp_env"  # noqa: S105 - test fixture value
FILE_TOKEN = "ghp_file"  # noqa: S105 - test fixture value


@pytest.fixture
def fake_env(tmp_path: pathlib.Path) -> dict[str, str]:
    """Environment with XDG bases redirected and no ambient credentials."""
    return {
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
        "XDG_STATE_HOME": str(tmp_path / "state"),
    }


def _write_credentials(env: dict[str, str], owner: str, body: str) -> pathlib.Path:
    path = xdg.owner_credentials_path(owner, env)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    path.chmod(0o600)
    return path


class TestLoadCredentials:
    """Reading the owner credentials file."""

    def test_missing_file_yields_empty_mapping(
        self,
        fake_env: dict[str, str],
    ) -> None:
        """No credentials file means no file-sourced credentials."""
        assert credentials.load_credentials("leynos", env=fake_env) == {}

    def test_known_keys_load_and_unknown_keys_are_ignored(
        self,
        fake_env: dict[str, str],
    ) -> None:
        """Only recognized credential keys are honoured."""
        _write_credentials(
            fake_env,
            "leynos",
            "GITHUB_TOKEN: ghp_file\nSCW_ACCESS_KEY: ak\nSCW_SECRET_KEY: sk\n"
            "UNRELATED: nope\n",
        )
        loaded = credentials.load_credentials("leynos", env=fake_env)
        assert loaded == {
            "GITHUB_TOKEN": "ghp_file",
            "SCW_ACCESS_KEY": "ak",
            "SCW_SECRET_KEY": "sk",
        }

    def test_group_readable_file_is_refused(
        self,
        fake_env: dict[str, str],
    ) -> None:
        """A credentials file other users can read fails closed."""
        path = _write_credentials(fake_env, "leynos", "GITHUB_TOKEN: ghp_file\n")
        path.chmod(0o640)
        with pytest.raises(credentials.InsecureCredentialsError):
            credentials.load_credentials("leynos", env=fake_env)


class TestCredentialEnvironment:
    """Merging process environment over file-backed fallbacks."""

    def test_environment_wins_over_file(self, fake_env: dict[str, str]) -> None:
        """A variable set in the environment shadows the file value."""
        _write_credentials(
            fake_env,
            "leynos",
            "GITHUB_TOKEN: ghp_file\nSCW_ACCESS_KEY: file-ak\n",
        )
        env = dict(fake_env)
        env["GITHUB_TOKEN"] = ENV_TOKEN
        merged = credentials.credential_environment(owner="leynos", env=env)
        assert merged["GITHUB_TOKEN"] == ENV_TOKEN
        assert merged["SCW_ACCESS_KEY"] == "file-ak"

    def test_no_owner_returns_plain_environment(
        self,
        fake_env: dict[str, str],
    ) -> None:
        """Without a resolvable owner the environment passes through."""
        env = dict(fake_env)
        env["GITHUB_TOKEN"] = ENV_TOKEN
        merged = credentials.credential_environment(owner=None, env=env)
        assert merged["GITHUB_TOKEN"] == ENV_TOKEN

    def test_active_owner_is_used_when_owner_omitted(
        self,
        fake_env: dict[str, str],
    ) -> None:
        """The headline active owner scopes the credentials file."""
        xdg.set_active_owner("leynos", fake_env)
        _write_credentials(fake_env, "leynos", "GITHUB_TOKEN: ghp_file\n")
        merged = credentials.credential_environment(env=fake_env)
        assert merged["GITHUB_TOKEN"] == FILE_TOKEN


class TestGithubToken:
    """The github_token convenience resolver."""

    def test_env_then_file_ordering(self, fake_env: dict[str, str]) -> None:
        """Environment beats file; file beats nothing."""
        _write_credentials(fake_env, "leynos", "GITHUB_TOKEN: ghp_file\n")
        xdg.set_active_owner("leynos", fake_env)
        assert credentials.github_token(env=fake_env) == FILE_TOKEN
        env = dict(fake_env)
        env["GITHUB_TOKEN"] = ENV_TOKEN
        assert credentials.github_token(env=env) == ENV_TOKEN

    def test_returns_none_when_absent(self, fake_env: dict[str, str]) -> None:
        """No env value and no file yields None."""
        assert credentials.github_token(env=fake_env) is None
