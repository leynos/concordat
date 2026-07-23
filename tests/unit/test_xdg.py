"""Unit tests for the XDG path layout and headline owner config."""

from __future__ import annotations

import typing as typ

import pytest

from concordat import xdg
from concordat.errors import ConcordatError

if typ.TYPE_CHECKING:
    import pathlib


@pytest.fixture
def fake_env(tmp_path: pathlib.Path) -> dict[str, str]:
    """Provide an environment mapping with all XDG bases redirected."""
    return {
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
        "XDG_STATE_HOME": str(tmp_path / "state"),
    }


class TestRoots:
    """Base directory resolution honours the XDG variables."""

    def test_roots_honour_xdg_env(
        self,
        tmp_path: pathlib.Path,
        fake_env: dict[str, str],
    ) -> None:
        """Each root lands under its redirected XDG base."""
        assert xdg.config_root(fake_env) == tmp_path / "config" / "concordat"
        assert xdg.cache_root(fake_env) == tmp_path / "cache" / "concordat"
        assert xdg.state_root(fake_env) == tmp_path / "state" / "concordat"

    def test_roots_fall_back_to_home_defaults(self) -> None:
        """Unset XDG variables fall back to the specification defaults."""
        env: dict[str, str] = {}
        assert str(xdg.config_root(env)).endswith(".config/concordat")
        assert str(xdg.cache_root(env)).endswith(".cache/concordat")
        assert str(xdg.state_root(env)).endswith(".local/state/concordat")


class TestOwnerPaths:
    """Owner-namespaced path construction."""

    def test_owner_paths_are_namespaced(
        self,
        tmp_path: pathlib.Path,
        fake_env: dict[str, str],
    ) -> None:
        """Config, credentials, cache, and runs nest under owners/<owner>."""
        owner = "leynos"
        assert xdg.owner_config_path(owner, fake_env) == (
            tmp_path / "config" / "concordat" / "owners" / owner / "config.yaml"
        )
        assert xdg.owner_credentials_path(owner, fake_env) == (
            tmp_path / "config" / "concordat" / "owners" / owner / "credentials.yaml"
        )
        assert xdg.owner_estates_cache_dir(owner, fake_env) == (
            tmp_path / "cache" / "concordat" / "owners" / owner / "estates"
        )
        assert xdg.owner_runs_dir(owner, fake_env) == (
            tmp_path / "state" / "concordat" / "owners" / owner / "runs"
        )

    def test_tofu_plugin_cache_dir_is_shared(
        self,
        tmp_path: pathlib.Path,
        fake_env: dict[str, str],
    ) -> None:
        """The provider plugin cache is owner-independent."""
        assert xdg.tofu_plugin_cache_dir(fake_env) == (
            tmp_path / "cache" / "concordat" / "tofu" / "plugin-cache"
        )

    @pytest.mark.parametrize(
        "owner",
        ["", "../escape", "a/b", ".hidden", "-lead", "trail-", "sp ace"],
    )
    def test_invalid_owner_names_are_rejected(self, owner: str) -> None:
        """Owner names that are not valid GitHub owners raise."""
        with pytest.raises(ConcordatError):
            xdg.owner_config_path(owner, {})


class TestHeadlineOwner:
    """The headline config file manages the active GitHub owner."""

    def test_active_owner_round_trips(self, fake_env: dict[str, str]) -> None:
        """set_active_owner persists and get_active_owner reads it back."""
        assert xdg.get_active_owner(fake_env) is None
        xdg.set_active_owner("leynos", fake_env)
        assert xdg.get_active_owner(fake_env) == "leynos"
        xdg.set_active_owner("df12", fake_env)
        assert xdg.get_active_owner(fake_env) == "df12"

    def test_set_active_owner_validates_name(
        self,
        fake_env: dict[str, str],
    ) -> None:
        """Invalid owner names never reach the headline file."""
        with pytest.raises(ConcordatError):
            xdg.set_active_owner("not/valid", fake_env)
        assert xdg.get_active_owner(fake_env) is None

    def test_headline_preserves_unknown_keys(
        self,
        tmp_path: pathlib.Path,
        fake_env: dict[str, str],
    ) -> None:
        """Rewriting the headline keeps keys other tools may have added."""
        headline = xdg.headline_config_path(fake_env)
        headline.parent.mkdir(parents=True)
        headline.write_text("future_key: kept\n")
        xdg.set_active_owner("leynos", fake_env)
        content = headline.read_text()
        assert "future_key: kept" in content
        assert "github_owner: leynos" in content
