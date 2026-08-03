"""Unit tests for estate configuration persistence and payload decoding."""

from __future__ import annotations

import typing as typ

import pytest

from concordat import estate, estate_config
from concordat.errors import ConcordatError
from concordat.estate import (
    EstateRecord,
    get_active_estate,
    list_estates,
    register_estate,
    set_active_estate,
)

if typ.TYPE_CHECKING:
    import pathlib


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
    assert estates == [record], (
        f"the registered estate should round-trip from the config, got {estates}"
    )

    active = get_active_estate(config_path=config_path)
    assert active == record, (
        f"the first registered estate should become active, got {active}"
    )


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
    assert updated == second, (
        f"switching should return the sandbox record, got {updated}"
    )
    active = get_active_estate(config_path=config_path)
    assert active == second, f"sandbox should become the active estate, got {active}"


class TestEstateRecordFromPayload:
    """Decoding of persisted estate payloads into records."""

    def test_legacy_string_payload_uses_defaults(self) -> None:
        """A bare repo-URL string is the legacy shorthand for a record."""
        record = estate_config._estate_record_from_payload(
            "core", "git@github.com:example/core.git"
        )
        assert record == EstateRecord(
            alias="core",
            repo_url="git@github.com:example/core.git",
            branch=estate_config.DEFAULT_BRANCH,
            inventory_path=estate_config.DEFAULT_INVENTORY_PATH,
            github_owner=None,
        ), record

    def test_mapping_payload_fills_omitted_optionals(self) -> None:
        """A mapping with only repo_url falls back to the declared defaults."""
        record = estate_config._estate_record_from_payload(
            "core", {"repo_url": "git@github.com:example/core.git"}
        )
        assert record is not None, "a mapping with a string repo_url should decode"
        assert record.branch == estate_config.DEFAULT_BRANCH, record.branch
        assert record.inventory_path == estate_config.DEFAULT_INVENTORY_PATH, (
            record.inventory_path
        )
        assert record.github_owner is None, record.github_owner

    def test_supplied_optionals_are_coerced_to_str(self) -> None:
        """Non-string branch and inventory_path values are stringified."""
        record = estate_config._estate_record_from_payload(
            "core",
            {
                "repo_url": "git@github.com:example/core.git",
                "branch": 2,
                "inventory_path": 7,
                "github_owner": "example",
            },
        )
        assert record is not None, "a mapping with a string repo_url should decode"
        assert record.branch == "2", record.branch
        assert record.inventory_path == "7", record.inventory_path
        assert record.github_owner == "example", record.github_owner

    def test_whitespace_owner_normalises_to_none(self) -> None:
        """A whitespace-only github_owner is normalised away."""
        record = estate_config._estate_record_from_payload(
            "core",
            {"repo_url": "git@github.com:example/core.git", "github_owner": "   "},
        )
        assert record is not None, "a mapping with a string repo_url should decode"
        assert record.github_owner is None, record.github_owner

    @pytest.mark.parametrize(
        "owner",
        [
            pytest.param(None, id="null"),
            pytest.param(7, id="integer"),
            pytest.param(False, id="boolean"),
            pytest.param([], id="list"),
            pytest.param({}, id="mapping"),
        ],
    )
    def test_non_string_owner_normalises_to_none(self, owner: object) -> None:
        """A non-string github_owner is ignored rather than crashing the load.

        The mapping pattern proves only that ``repo_url`` is a string, so the
        owner is still whatever YAML decoded. Anything but a string reached
        ``_normalise_owner`` and died on ``.strip()``, taking down every
        command that reads the config rather than skipping one bad field.
        """
        record = estate_config._estate_record_from_payload(
            "core",
            {"repo_url": "git@github.com:example/core.git", "github_owner": owner},
        )
        assert record is not None, "a mapping with a string repo_url should decode"
        assert record.github_owner is None, record.github_owner

    @pytest.mark.parametrize(
        "payload",
        [
            pytest.param(["git@github.com:example/core.git"], id="list"),
            pytest.param(7, id="integer"),
            pytest.param(None, id="none"),
            pytest.param({}, id="mapping-without-repo-url"),
            pytest.param({"repo_url": 7}, id="mapping-with-non-string-repo-url"),
            pytest.param({"repo_url": None}, id="mapping-with-null-repo-url"),
        ],
    )
    def test_unsupported_payloads_are_rejected(self, payload: object) -> None:
        """Unsupported payloads decode to None rather than raising."""
        assert estate_config._estate_record_from_payload("core", payload) is None, (
            f"payload {payload!r} should be rejected"
        )

    def test_invalid_payloads_are_omitted_from_the_collection(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        """`_load_estates` skips undecodable entries and keeps the valid ones."""
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            "estate:\n"
            "  estates:\n"
            "    legacy: git@github.com:example/legacy.git\n"
            "    good:\n"
            "      repo_url: git@github.com:example/good.git\n"
            "    listy:\n"
            "      - git@github.com:example/listy.git\n"
            "    numeric: 7\n"
            "    bad_url:\n"
            "      repo_url: 7\n",
            encoding="utf-8",
        )
        records = estate_config._load_estates(config_path)
        assert sorted(records) == ["good", "legacy"], sorted(records)

    def test_non_mapping_estate_collection_yields_no_records(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        """An `estates` value that is not a mapping loads as empty."""
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            "estate:\n  estates:\n    - not-a-mapping\n", encoding="utf-8"
        )
        assert estate_config._load_estates(config_path) == {}, "expected no records"


class TestEstateConfigReexport:
    """Configuration APIs stay importable from the ``concordat.estate`` façade."""

    def test_estate_record_is_the_same_class(self) -> None:
        """``estate.EstateRecord`` is the class defined in ``estate_config``."""
        from concordat import estate_config

        assert estate.EstateRecord is estate_config.EstateRecord, (
            "estate.EstateRecord should be estate_config.EstateRecord"
        )

    def test_config_apis_are_reexported(self) -> None:
        """Moved public config symbols remain reachable through ``estate``."""
        from concordat import estate_config

        for name in (
            "default_config_path",
            "list_estates",
            "get_estate",
            "get_active_estate",
            "set_active_estate",
            "register_estate",
            "CONFIG_FILENAME",
            "DEFAULT_BRANCH",
            "DEFAULT_INVENTORY_PATH",
        ):
            assert getattr(estate, name) is getattr(estate_config, name), (
                f"estate.{name} should be the same object as estate_config.{name}"
            )

    def test_register_estate_remains_callable(self, tmp_path: pathlib.Path) -> None:
        """``estate.register_estate`` still persists an estate to a config file."""
        config_path = tmp_path / "config.yaml"
        record = estate.EstateRecord(
            alias="core",
            repo_url="git@github.com:example/core.git",
        )
        estate.register_estate(record, config_path=config_path)
        loaded = estate.get_estate("core", config_path=config_path)
        assert loaded == record, f"persisted estate should round-trip: {loaded}"


def test_yaml_config_load_raises_estate_error(
    tmp_path: pathlib.Path,
) -> None:
    """Malformed YAML in the config provider raises a descriptive EstateError.

    Cyclopts re-wraps this into a controlled ``CycloptsError`` at the CLI
    boundary, so the provider method is exercised directly to verify the
    domain error and message it produces.
    """
    from concordat import estate_config

    bad = tmp_path / "config.yaml"
    bad.write_text("estate: [unterminated\n")
    provider = estate_config._YamlConfig(path=str(bad), must_exist=False)
    with pytest.raises(
        estate.EstateError, match="cannot read estate configuration"
    ) as exc_info:
        provider._load_config(bad)
    assert isinstance(exc_info.value, ConcordatError), (
        "EstateError must be a ConcordatError subtype"
    )


class TestNonMappingEstateSection:
    """A persisted non-mapping `estate:` must not break the write paths."""

    @pytest.mark.parametrize(
        "section",
        [
            pytest.param("just-a-string", id="string"),
            pytest.param("[1, 2]", id="list"),
            pytest.param("7", id="integer"),
        ],
    )
    def test_write_paths_replace_a_non_mapping_section(
        self,
        tmp_path: pathlib.Path,
        section: str,
    ) -> None:
        """Registering an estate over a malformed section succeeds.

        `setdefault` returns an existing non-mapping value untouched, so the
        write paths used to mutate a string or list and raise `TypeError` or
        `AttributeError`. They now replace it, as the read paths already did.
        """
        config_path = tmp_path / "config.yaml"
        config_path.write_text(f"estate: {section}\n", encoding="utf-8")

        estate_config.register_estate(
            EstateRecord(alias="core", repo_url="git@github.com:example/core.git"),
            config_path=config_path,
            set_active_if_missing=True,
        )

        records = estate_config.list_estates(config_path=config_path)
        assert [record.alias for record in records] == ["core"], records
