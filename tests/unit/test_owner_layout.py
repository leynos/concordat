"""Unit tests for owner-namespaced estate config resolution and migration."""

from __future__ import annotations

import typing as typ

import pytest

from concordat import estate, estate_cache, estate_config, xdg
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

        estate.migrate_legacy_config()
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

    def test_failed_cleanup_leaves_migrated_estates_reachable(
        self,
        xdg_env: dict[str, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A failed legacy cleanup duplicates estate data rather than hiding it.

        Cleanup is the last step precisely so it is the only one that may fail.
        The active owner is already set by then, so ``default_config_path``
        still selects the complete owner-scoped file even though the legacy
        section survives.
        """
        legacy = xdg.config_root() / estate.CONFIG_FILENAME
        legacy.parent.mkdir(parents=True)
        legacy.write_text(LEGACY_CONFIG)

        def refuse_cleanup(*_args: object, **_kwargs: object) -> typ.NoReturn:
            message = "cleanup denied"
            raise OSError(message)

        monkeypatch.setattr(
            estate_config,
            "_remove_legacy_estate_section",
            refuse_cleanup,
        )

        with pytest.raises(OSError, match="cleanup denied"):
            estate.migrate_legacy_config()

        assert xdg.get_active_owner() == "leynos", xdg.get_active_owner()
        records = estate.list_estates()
        assert [record.alias for record in records] == ["prod"], records
        # The duplicate is the accepted cost: the data is reachable, not lost.
        assert "estates:" in legacy.read_text(), legacy.read_text()

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

        estate.migrate_legacy_config()

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

        estate.migrate_legacy_config()

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
            estate.migrate_legacy_config()

    def test_mixed_owner_legacy_config_is_rejected(
        self,
        xdg_env: dict[str, str],
    ) -> None:
        """A legacy config spanning multiple owners is refused, not misplaced."""
        legacy = xdg.config_root() / estate.CONFIG_FILENAME
        legacy.parent.mkdir(parents=True)
        legacy.write_text(
            "estate:\n"
            "  estates:\n"
            "    prod:\n"
            "      repo_url: git@github.com:leynos/prod.git\n"
            "      github_owner: leynos\n"
            "    other:\n"
            "      repo_url: git@github.com:someone/other.git\n"
            "      github_owner: someone\n"
        )

        with pytest.raises(
            estate.EstateError, match="multiple github owners"
        ) as exc_info:
            estate.migrate_legacy_config()
        message = str(exc_info.value)
        assert "leynos" in message, (
            "the error should name owner 'leynos' so the operator can split them"
        )
        assert "someone" in message, (
            "the error should name owner 'someone' so the operator can split them"
        )
        # Nothing was migrated: no owner file was written for either owner.
        assert not xdg.owner_config_path("leynos").exists(), (
            "leynos's estate config must not be written on a rejected migration"
        )
        assert not xdg.owner_config_path("someone").exists(), (
            "someone's estate config must not be written on a rejected migration"
        )


class TestActiveOwnerForImplicitConfig:
    """`init_estate` resolves the owner-scoped config without committing early.

    The resolver is exercised directly for the resolution/validation invariant;
    the deferred activation — the active owner is committed only after a
    successful registration — is covered by the duplicate-alias regression
    driven through `init_estate`.
    """

    def test_implicit_path_reports_owner_without_activating(
        self,
        xdg_env: dict[str, str],
    ) -> None:
        """With no active owner, the resolver reports it but does not commit it."""
        path, owner_to_activate = estate._resolve_implicit_config_path(None, "leynos")
        assert path == xdg.owner_config_path("leynos"), (
            f"implicit path should resolve to the owner-scoped config, got {path!r}"
        )
        assert owner_to_activate == "leynos", (
            "resolver should report the estate owner to activate, got "
            f"{owner_to_activate!r}"
        )
        assert xdg.get_active_owner() is None, (
            f"resolution must not activate an owner, got {xdg.get_active_owner()!r}"
        )

    def test_explicit_path_has_no_side_effect(
        self,
        xdg_env: dict[str, str],
        tmp_path: pathlib.Path,
    ) -> None:
        """An explicit config path bypasses the owner namespace entirely."""
        explicit = tmp_path / "explicit.yaml"
        path, owner_to_activate = estate._resolve_implicit_config_path(
            explicit, "leynos"
        )
        assert path == explicit, (
            f"an explicit config path should be returned unchanged, got {path!r}"
        )
        assert owner_to_activate is None, (
            "an explicit path must not schedule owner activation, got "
            f"{owner_to_activate!r}"
        )
        assert xdg.get_active_owner() is None, (
            "an explicit path must not touch the active owner, got "
            f"{xdg.get_active_owner()!r}"
        )

    def test_existing_active_owner_is_not_overwritten(
        self,
        xdg_env: dict[str, str],
    ) -> None:
        """A matching active owner needs no re-activation."""
        xdg.set_active_owner("leynos")
        path, owner_to_activate = estate._resolve_implicit_config_path(None, "leynos")
        assert path == xdg.owner_config_path("leynos"), (
            f"a matching owner should resolve to its config path, got {path!r}"
        )
        assert owner_to_activate is None, (
            f"an already-active owner needs no re-activation, got {owner_to_activate!r}"
        )
        assert xdg.get_active_owner() == "leynos", (
            "the matching active owner should be preserved, got "
            f"{xdg.get_active_owner()!r}"
        )

    def test_mismatched_active_owner_is_refused(
        self,
        xdg_env: dict[str, str],
    ) -> None:
        """An estate is never registered under a different active owner."""
        xdg.set_active_owner("df12")
        with pytest.raises(estate.ActiveOwnerMismatchError):
            estate._resolve_implicit_config_path(None, "leynos")
        assert xdg.get_active_owner() == "df12", (
            "a rejected owner mismatch must preserve the active owner, got "
            f"{xdg.get_active_owner()!r}"
        )

    def test_duplicate_alias_via_implicit_path_leaves_owner_unset(
        self,
        xdg_env: dict[str, str],
    ) -> None:
        """A duplicate-alias failure on the implicit path leaves no active owner."""
        # Seed the owner's config with an estate that collides on alias. The
        # duplicate-alias check fires before any GitHub/git interaction, so no
        # remote mocking is needed to reach the failure.
        estate.register_estate(
            estate.EstateRecord(
                alias="prod",
                repo_url="git@github.com:leynos/prod.git",
                github_owner="leynos",
            ),
            config_path=xdg.owner_config_path("leynos"),
        )
        assert xdg.get_active_owner() is None, "seeding must not activate an owner"

        with pytest.raises(estate.DuplicateEstateAliasError):
            estate.init_estate(
                "prod",
                "git@github.com:leynos/prod.git",
                confirm=lambda _prompt: True,
            )
        assert xdg.get_active_owner() is None, (
            "a duplicate-alias failure must leave no active owner, got "
            f"{xdg.get_active_owner()!r}"
        )


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
        assert not destination.parent.exists(), (
            "resolving a cache path must not create it: "
            f"{destination.parent} should still be absent"
        )

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
