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
    """Write *body* as *owner*'s credentials file and return its path."""
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
        loaded = credentials.load_credentials("leynos", env=fake_env)
        assert loaded == {}, f"missing credentials file should load to empty: {loaded}"

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
        }, f"loading should keep only recognized keys: {loaded}"

    def test_non_string_values_are_ignored(
        self,
        fake_env: dict[str, str],
    ) -> None:
        """Only string values count as credentials.

        Coercing would be worse than dropping: an empty ``GITHUB_TOKEN:``
        parses as ``None`` and would become the literal secret ``"None"``,
        which concordat would then present to the remote.
        """
        _write_credentials(
            fake_env,
            "leynos",
            "GITHUB_TOKEN:\nAWS_ACCESS_KEY_ID: false\nAWS_SESSION_TOKEN: 12345\n"
            "SCW_ACCESS_KEY: ak\n",
        )
        loaded = credentials.load_credentials("leynos", env=fake_env)
        assert loaded == {"SCW_ACCESS_KEY": "ak"}, (
            f"null, boolean, and numeric values should be dropped: {loaded}"
        )

    def test_group_readable_file_is_refused(
        self,
        fake_env: dict[str, str],
    ) -> None:
        """A credentials file other users can read fails closed."""
        path = _write_credentials(fake_env, "leynos", "GITHUB_TOKEN: ghp_file\n")
        path.chmod(0o640)
        with pytest.raises(credentials.InsecureCredentialsError):
            credentials.load_credentials("leynos", env=fake_env)

    def test_malformed_yaml_raises_concordat_error(
        self,
        fake_env: dict[str, str],
    ) -> None:
        """A YAML syntax error surfaces as a catchable ConcordatError."""
        _write_credentials(fake_env, "leynos", "GITHUB_TOKEN: [unterminated\n")
        with pytest.raises(
            credentials.MalformedCredentialsError, match="cannot read credentials"
        ) as exc_info:
            credentials.load_credentials("leynos", env=fake_env)
        # The CLI boundary catches ConcordatError; the domain error must be one.
        assert isinstance(exc_info.value, credentials.ConcordatError), (
            "MalformedCredentialsError must reach the CLI as a ConcordatError"
        )


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
        assert merged["GITHUB_TOKEN"] == ENV_TOKEN, (
            f"env GITHUB_TOKEN should win over the file: {merged['GITHUB_TOKEN']!r}"
        )
        assert merged["SCW_ACCESS_KEY"] == "file-ak", (
            f"file SCW_ACCESS_KEY should fill the gap: {merged['SCW_ACCESS_KEY']!r}"
        )

    def test_no_owner_returns_plain_environment(
        self,
        fake_env: dict[str, str],
    ) -> None:
        """Without a resolvable owner the environment passes through."""
        env = dict(fake_env)
        env["GITHUB_TOKEN"] = ENV_TOKEN
        merged = credentials.credential_environment(owner=None, env=env)
        assert merged["GITHUB_TOKEN"] == ENV_TOKEN, (
            f"no-owner path should pass the env token through: "
            f"{merged['GITHUB_TOKEN']!r}"
        )

    def test_active_owner_is_used_when_owner_omitted(
        self,
        fake_env: dict[str, str],
    ) -> None:
        """The headline active owner scopes the credentials file."""
        xdg.set_active_owner("leynos", fake_env)
        _write_credentials(fake_env, "leynos", "GITHUB_TOKEN: ghp_file\n")
        merged = credentials.credential_environment(env=fake_env)
        assert merged["GITHUB_TOKEN"] == FILE_TOKEN, (
            f"active owner should scope the file token: {merged['GITHUB_TOKEN']!r}"
        )


class TestGithubToken:
    """The github_token convenience resolver."""

    def test_env_then_file_ordering(self, fake_env: dict[str, str]) -> None:
        """Environment beats file; file beats nothing."""
        _write_credentials(fake_env, "leynos", "GITHUB_TOKEN: ghp_file\n")
        xdg.set_active_owner("leynos", fake_env)
        file_token = credentials.github_token(env=fake_env)
        assert file_token == FILE_TOKEN, (
            f"file token should resolve when the env var is unset: {file_token!r}"
        )
        env = dict(fake_env)
        env["GITHUB_TOKEN"] = ENV_TOKEN
        env_token = credentials.github_token(env=env)
        assert env_token == ENV_TOKEN, (
            f"env token should beat the file token: {env_token!r}"
        )

    def test_returns_none_when_absent(self, fake_env: dict[str, str]) -> None:
        """No env value and no file yields None."""
        token = credentials.github_token(env=fake_env)
        assert token is None, f"absent env and file should yield None: {token!r}"
