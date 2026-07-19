"""Unit tests for owner-namespaced estate config resolution and migration."""

from __future__ import annotations

import typing as typ

import pytest

from concordat import estate, estate_cache, xdg
from concordat.estate import EstateRecord
from concordat.estate_cache import EstateCacheError

if typ.TYPE_CHECKING:
    import pathlib

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
) -> dict[str, str]:
    """Redirect every XDG base into the test's temporary directory."""
    mapping = {
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
        "XDG_STATE_HOME": str(tmp_path / "state"),
    }
    for key, value in mapping.items():
        monkeypatch.setenv(key, value)
    return mapping


class TestDefaultConfigPath:
    """default_config_path resolves through the headline owner."""

    def test_active_owner_scopes_the_config(
        self,
        xdg_env: dict[str, str],
    ) -> None:
        """With an active owner, config lives under owners/<owner>/."""
        xdg.set_active_owner("leynos")
        assert estate.default_config_path() == xdg.owner_config_path("leynos")

    def test_without_owner_falls_back_to_headline_directory(
        self,
        xdg_env: dict[str, str],
    ) -> None:
        """With no owner configured, the legacy flat path is used."""
        assert estate.default_config_path() == (
            xdg.config_root() / estate.CONFIG_FILENAME
        )

    def test_legacy_config_migrates_to_owner_layout(
        self,
        xdg_env: dict[str, str],
    ) -> None:
        """A legacy flat config migrates once the owner is derivable."""
        legacy = xdg.config_root() / estate.CONFIG_FILENAME
        legacy.parent.mkdir(parents=True)
        legacy.write_text(LEGACY_CONFIG)

        resolved = estate.default_config_path()

        assert resolved == xdg.owner_config_path("leynos")
        assert xdg.get_active_owner() == "leynos"
        records = estate.list_estates()
        assert [record.alias for record in records] == ["prod"]
        active = estate.get_active_estate()
        assert active is not None
        assert active.alias == "prod"
        # The headline file no longer carries the estate section.
        assert "estates:" not in legacy.read_text()


class TestOwnerNamespacedCache:
    """Estate caches nest under the owning GitHub owner."""

    def test_cache_destination_uses_record_owner(
        self,
        xdg_env: dict[str, str],
    ) -> None:
        """A record's github_owner namespaces its cache path."""
        record = EstateRecord(
            alias="prod",
            repo_url="git@github.com:leynos/df12-std-prod.git",
            github_owner="leynos",
        )
        destination = estate_cache.cache_destination(record)
        assert destination == xdg.owner_estates_cache_dir("leynos") / "prod"

    def test_cache_destination_falls_back_to_active_owner(
        self,
        xdg_env: dict[str, str],
    ) -> None:
        """A record without an owner uses the headline active owner."""
        xdg.set_active_owner("df12")
        record = EstateRecord(
            alias="prod",
            repo_url="git@github.com:leynos/df12-std-prod.git",
            github_owner=None,
        )
        destination = estate_cache.cache_destination(record)
        assert destination == xdg.owner_estates_cache_dir("df12") / "prod"

    def test_cache_destination_requires_an_owner(
        self,
        xdg_env: dict[str, str],
    ) -> None:
        """No record owner and no active owner is an error."""
        record = EstateRecord(
            alias="prod",
            repo_url="git@github.com:leynos/df12-std-prod.git",
            github_owner=None,
        )
        with pytest.raises(EstateCacheError, match="owner"):
            estate_cache.cache_destination(record)
