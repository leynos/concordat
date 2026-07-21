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

    def test_migration_preserves_non_estate_headline_keys(
        self,
        xdg_env: dict[str, str],
    ) -> None:
        """Headline keys outside the estate section survive migration."""
        legacy = xdg.config_root() / estate.CONFIG_FILENAME
        legacy.parent.mkdir(parents=True)
        # ``telemetry`` is an arbitrary non-estate headline key; it must not be
        # ``github_owner`` (the active-owner key), which would skip migration.
        legacy.write_text(f"telemetry: disabled\n{LEGACY_CONFIG}")

        estate.default_config_path()

        remaining = legacy.read_text()
        assert "telemetry: disabled" in remaining
        assert "estates:" not in remaining

    def test_existing_active_owner_skips_migration(
        self,
        xdg_env: dict[str, str],
    ) -> None:
        """A configured active owner leaves the legacy config untouched."""
        # The legacy flat file and the headline file are the same path, so a
        # configured active owner lives alongside the estate section here.
        legacy = xdg.config_root() / estate.CONFIG_FILENAME
        legacy.parent.mkdir(parents=True)
        contents = f"github_owner: someone-else\n{LEGACY_CONFIG}"
        legacy.write_text(contents)
        assert xdg.get_active_owner() == "someone-else"

        estate.default_config_path()

        assert legacy.read_text() == contents
        assert xdg.get_active_owner() == "someone-else"

    def test_malformed_legacy_config_raises(
        self,
        xdg_env: dict[str, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Unreadable legacy YAML surfaces as an EstateError.

        The legacy flat file and the headline file share a path, so an active
        owner lookup would otherwise read the malformed file first. Forcing no
        active owner isolates the migration's own read-failure branch.
        """
        monkeypatch.setattr(xdg, "get_active_owner", lambda *_, **__: None)
        legacy = xdg.config_root() / estate.CONFIG_FILENAME
        legacy.parent.mkdir(parents=True)
        legacy.write_text("estate: [unterminated\n")

        with pytest.raises(
            estate.EstateError, match="cannot read legacy configuration"
        ):
            estate.default_config_path()


class TestActiveOwnerForImplicitConfig:
    """`init_estate` settles the active owner before resolving its config.

    The helper is exercised directly: it is the whole of the invariant, and
    driving it through `init_estate` would require mocking GitHub and the
    template bootstrap without covering anything further.
    """

    def test_implicit_path_records_the_estate_owner(
        self,
        xdg_env: dict[str, str],
    ) -> None:
        """With no active owner, the estate owner becomes the active owner."""
        resolved = estate._ensure_active_owner_for_implicit_config(None, "leynos")
        assert xdg.get_active_owner() == "leynos"
        assert resolved == xdg.owner_config_path("leynos")

    def test_explicit_path_has_no_side_effect(
        self,
        xdg_env: dict[str, str],
        tmp_path: pathlib.Path,
    ) -> None:
        """An explicit config path bypasses the owner namespace entirely."""
        explicit = tmp_path / "explicit.yaml"
        resolved = estate._ensure_active_owner_for_implicit_config(explicit, "leynos")
        assert resolved == explicit
        assert xdg.get_active_owner() is None

    def test_existing_active_owner_is_not_overwritten(
        self,
        xdg_env: dict[str, str],
    ) -> None:
        """A matching active owner is left exactly as configured."""
        xdg.set_active_owner("leynos")
        resolved = estate._ensure_active_owner_for_implicit_config(None, "leynos")
        assert xdg.get_active_owner() == "leynos"
        assert resolved == xdg.owner_config_path("leynos")

    def test_mismatched_active_owner_is_refused(
        self,
        xdg_env: dict[str, str],
    ) -> None:
        """An estate is never registered under a different active owner."""
        xdg.set_active_owner("df12")
        with pytest.raises(estate.ActiveOwnerMismatchError):
            estate._ensure_active_owner_for_implicit_config(None, "leynos")
        assert xdg.get_active_owner() == "df12"


class TestOwnerNamespacedCache:
    """Estate caches nest under the owning GitHub owner."""

    @pytest.mark.parametrize(
        ("record_owner", "active_owner", "expected_owner"),
        [
            pytest.param(
                "leynos",
                "df12",
                "leynos",
                id="record-owner-takes-precedence",
            ),
            pytest.param(
                None,
                "df12",
                "df12",
                id="falls-back-to-active-owner",
            ),
        ],
    )
    def test_cache_destination_resolves_owner(
        self,
        xdg_env: dict[str, str],
        record_owner: str | None,
        active_owner: str,
        expected_owner: str,
    ) -> None:
        """The record's owner wins; the headline active owner fills the gap."""
        xdg.set_active_owner(active_owner)
        record = EstateRecord(
            alias="prod",
            repo_url="git@github.com:leynos/df12-std-prod.git",
            github_owner=record_owner,
        )
        destination = estate_cache.cache_destination(record)
        assert destination == xdg.owner_estates_cache_dir(expected_owner) / "prod"

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
