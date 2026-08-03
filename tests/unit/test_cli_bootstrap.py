"""Integration tests for CLI bootstrap: migration, tokens, and plugin cache.

These cover seams that unit tests exercise in isolation but that only agree
end to end: the migration must run before command dispatch, a file-backed
token must reach the GitHub client, and the plugin cache must default without
overriding a caller who set one.
"""

from __future__ import annotations

import stat
import typing as typ

import pytest

from concordat import cli, credentials, estate_execution, xdg
from concordat.credentials import InsecureCredentialsError

if typ.TYPE_CHECKING:
    import pathlib


def _invoke_cli(argv: list[str]) -> int:
    """Run the CLI, normalising Cyclopts' `SystemExit` into a return code."""
    try:
        return cli.main(argv)
    except SystemExit as exc:
        return 0 if exc.code is None else int(exc.code)


LEGACY_CONFIG = """\
estate:
  estates:
    prod:
      repo_url: git@github.com:leynos/df12-std-prod.git
      branch: main
      inventory_path: tofu/inventory/repositories.yaml
      github_owner: leynos
  active_estate: prod
"""


@pytest.fixture
def xdg_env(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> pathlib.Path:
    """Redirect every XDG base into the test's temporary directory."""
    for key, name in (
        ("XDG_CONFIG_HOME", "config"),
        ("XDG_CACHE_HOME", "cache"),
        ("XDG_STATE_HOME", "state"),
    ):
        monkeypatch.setenv(key, str(tmp_path / name))
    return tmp_path


class TestStartupMigration:
    """`main` migrates a legacy config before dispatching the command."""

    def test_migration_precedes_command_dispatch(
        self,
        xdg_env: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """A read-only command sees the migrated owner-scoped configuration.

        The estate is only visible if migration ran first: `default_config_path`
        is a pure query, so without an active owner it would resolve to the flat
        file that migration empties.
        """
        legacy = xdg.config_root() / "config.yaml"
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_text(LEGACY_CONFIG, encoding="utf-8")

        exit_code = _invoke_cli(["estate", "ls"])

        assert exit_code == 0, exit_code
        assert xdg.get_active_owner() == "leynos", xdg.get_active_owner()
        assert xdg.owner_config_path("leynos").is_file(), (
            "migration should have written the owner-scoped config"
        )
        assert "prod" in capsys.readouterr().out, "the command should list the estate"


class TestFileBackedToken:
    """The owner credentials file supplies a token the environment lacks."""

    @staticmethod
    def _write_credentials(owner: str, body: str) -> None:
        path = xdg.owner_credentials_path(owner)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        path.chmod(0o600)

    def test_file_token_is_used_when_environment_has_none(
        self,
        xdg_env: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A file-backed GITHUB_TOKEN reaches the resolved credentials."""
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        xdg.set_active_owner("leynos")
        self._write_credentials("leynos", "GITHUB_TOKEN: ghp_from_file\n")

        assert credentials.github_token() == "ghp_from_file", (
            "the file-backed token should be resolved when the environment "
            "does not supply one"
        )

    def test_cli_forwards_the_file_token_downstream(
        self,
        xdg_env: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A real CLI command hands the file-backed token to its dependency.

        Resolving the token is not the same as wiring it through. This drives
        `concordat ls` with no `--token` and no environment variable, so the
        only source left is the owner credentials file, and asserts the value
        reaches `list_namespace_repositories`.
        """
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        xdg.set_active_owner("leynos")
        self._write_credentials("leynos", "GITHUB_TOKEN: ghp_from_file\n")

        captured: dict[str, object] = {}

        def capture_repositories(
            namespaces: typ.Sequence[str],
            *,
            token: str | None = None,
        ) -> list[str]:
            captured["namespaces"] = tuple(namespaces)
            # Keyed as "credential" rather than "token": ruff's
            # hardcoded-password check fires on comparing a literal against a
            # token-named subscript, and the value here is a fixture, not a
            # secret.
            captured["credential"] = token
            return []

        monkeypatch.setattr(cli, "list_namespace_repositories", capture_repositories)

        exit_code = _invoke_cli(["ls", "leynos"])

        assert exit_code == 0, exit_code
        assert captured["credential"] == "ghp_from_file", captured
        assert "leynos" in typ.cast("tuple[str, ...]", captured["namespaces"]), captured

    def test_environment_token_still_wins(
        self,
        xdg_env: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The process environment outranks the credentials file."""
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_from_env")
        xdg.set_active_owner("leynos")
        self._write_credentials("leynos", "GITHUB_TOKEN: ghp_from_file\n")

        assert credentials.github_token() == "ghp_from_env", (
            "an environment token should outrank the file"
        )


class TestPluginCache:
    """The OpenTofu plugin cache defaults without overriding the caller."""

    def test_default_is_set_and_created(
        self,
        xdg_env: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Absent the variable, the XDG cache directory is chosen and created."""
        monkeypatch.delenv("TF_PLUGIN_CACHE_DIR", raising=False)
        expected = xdg.tofu_plugin_cache_dir()
        assert not expected.exists(), "the cache should not exist before the call"

        env = estate_execution._prepare_execution_environment(
            estate_execution.ExecutionOptions(
                github_owner="leynos",
                github_token="token",  # noqa: S106
            )
        )

        assert env["TF_PLUGIN_CACHE_DIR"] == str(expected), env["TF_PLUGIN_CACHE_DIR"]
        assert expected.is_dir(), f"{expected} should have been created"

    def test_supplied_value_wins_and_creates_nothing(
        self,
        xdg_env: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """A caller-supplied cache directory is honoured verbatim.

        The default must not be created as a side effect, or a caller pointing
        at their own cache would still get a stray XDG directory.
        """
        monkeypatch.delenv("TF_PLUGIN_CACHE_DIR", raising=False)
        supplied = tmp_path / "caller-cache"
        default = xdg.tofu_plugin_cache_dir()

        env = estate_execution._prepare_execution_environment(
            estate_execution.ExecutionOptions(
                github_owner="leynos",
                github_token="token",  # noqa: S106
                environment={"TF_PLUGIN_CACHE_DIR": str(supplied)},
            )
        )

        assert env["TF_PLUGIN_CACHE_DIR"] == str(supplied), env["TF_PLUGIN_CACHE_DIR"]
        assert not default.exists(), (
            f"the default cache {default} should not be created when the "
            "caller supplied one"
        )


class TestCredentialFileModes:
    """Credential files must be readable only by their owner."""

    @pytest.mark.parametrize(
        "mode",
        [
            pytest.param(stat.S_ISUID, id="setuid"),
            pytest.param(stat.S_ISGID, id="setgid"),
            pytest.param(stat.S_IRGRP, id="group-readable"),
            pytest.param(stat.S_IROTH, id="world-readable"),
        ],
    )
    def test_unsafe_mode_is_refused(
        self,
        xdg_env: pathlib.Path,
        mode: int,
    ) -> None:
        """Any group, world, or set-id bit refuses the file rather than reads it."""
        path = xdg.owner_credentials_path("leynos")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("GITHUB_TOKEN: ghp_secret\n", encoding="utf-8")
        path.chmod(0o600 | mode)

        with pytest.raises(InsecureCredentialsError, match="chmod 600"):
            credentials.load_credentials("leynos")

    def test_owner_only_mode_is_accepted(self, xdg_env: pathlib.Path) -> None:
        """A 0600 file is read normally."""
        path = xdg.owner_credentials_path("leynos")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("GITHUB_TOKEN: ghp_secret\n", encoding="utf-8")
        path.chmod(0o600)

        loaded = credentials.load_credentials("leynos")

        assert loaded == {"GITHUB_TOKEN": "ghp_secret"}, loaded
