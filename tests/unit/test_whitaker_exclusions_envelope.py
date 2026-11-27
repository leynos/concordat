"""Unit tests for the `policy-input/whitaker-exclusions` envelope builder.

The Rego suite proves what the policy makes of each envelope; these tests prove
what the builder records: where each exclusion is, whether a comment states its
reason, how a crate name resolves, and what it refuses to guess.
"""

from __future__ import annotations

import typing as typ

import pytest

from concordat.rules.whitaker_exclusions_envelope import (
    ENVELOPE_KIND,
    build_whitaker_exclusions_envelope,
)
from concordat.rules.whitaker_exclusions_text import locate_exclusions

if typ.TYPE_CHECKING:
    import pathlib

MANIFEST: typ.Final = '[package]\nname = "{name}"\nversion = "0.1.0"\n{extra}'


def _write(checkout: pathlib.Path, files: dict[str, str]) -> None:
    """Write *files* under *checkout*, creating directories."""
    for relative, text in files.items():
        path = checkout / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def _exclusions(
    checkout: pathlib.Path, files: dict[str, str]
) -> list[dict[str, typ.Any]]:
    """Build the envelope for *files* and return its exclusions."""
    _write(checkout, files)
    envelope = build_whitaker_exclusions_envelope(checkout)
    return typ.cast("list[dict[str, typ.Any]]", envelope["exclusions"])


class TestReasons:
    """Where a reason may sit, and what is not one."""

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ('[t]\n# why\nexcluded_paths = ["a"]\n', True),
            ('# why\n[t]\nexcluded_paths = ["a"]\n', True),
            ('[t]\nexcluded_paths = ["a"]\n', False),
            ('[t]\n# why\n\nexcluded_paths = ["a"]\n', False),
            ('[t]\n#\n#   \nexcluded_paths = ["a"]\n', False),
            ('# why\n\n[t]\nexcluded_paths = ["a"]\n', False),
            ('# why\nexcluded_paths = ["a"]\n', True),
            ('[t]\n  # indented why\n  excluded_paths = ["a"]\n', True),
        ],
    )
    def test_a_reason_is_a_non_empty_comment_block_directly_above(
        self, text: str, *, expected: bool
    ) -> None:
        """A blank line ends a block; a bare marker says nothing."""
        [found] = locate_exclusions(text)
        assert found.has_reason is expected

    def test_the_table_and_line_of_each_key_are_recorded(self) -> None:
        """The finding names the key's line in the file."""
        text = '[a]\nexcluded_paths = ["x"]\n\n[b.c]\nexcluded_crates = ["y"]\n'
        found = locate_exclusions(text)
        assert [(f.table, f.key, f.line) for f in found] == [
            ("a", "excluded_paths", 2),
            ("b.c", "excluded_crates", 5),
        ]

    def test_a_key_that_is_not_at_the_start_of_a_line_is_not_found(self) -> None:
        """A dotted key is left for the policy to report as not located."""
        assert locate_exclusions('a.excluded_crates = ["x"]\n') == []


class TestEnvelope:
    """What the builder records for a checkout."""

    def test_a_checkout_without_a_dylint_config_has_no_exclusions(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Nothing to judge."""
        envelope = build_whitaker_exclusions_envelope(tmp_path)
        assert envelope["kind"] == ENVELOPE_KIND
        assert envelope["exclusions"] == []

    def test_an_exclusion_records_its_place_reason_and_entries(
        self, tmp_path: pathlib.Path
    ) -> None:
        """The facts the policy reads."""
        found = _exclusions(
            tmp_path,
            {
                "Cargo.toml": MANIFEST.format(name="app", extra=""),
                "dylint.toml": '[lint]\n# because\nexcluded_paths = ["loader"]\n',
            },
        )
        assert found == [
            {
                "file": "dylint.toml",
                "table": "lint",
                "key": "excluded_paths",
                "kind": "paths",
                "line": 3,
                "has_reason": True,
                "entries": [{"name": "loader", "resolved": "unknown"}],
            }
        ]

    def test_a_dylint_config_in_a_fixture_tree_is_not_read(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Test data is not the repository's configuration."""
        found = _exclusions(
            tmp_path,
            {"tests/fixtures/x/dylint.toml": '[l]\nexcluded_paths = ["a"]\n'},
        )
        assert found == []


class TestCrateNames:
    """How an excluded crate name resolves."""

    def _resolved(
        self, checkout: pathlib.Path, files: dict[str, str]
    ) -> dict[str, str]:
        """Return each crate name of the sole exclusion, with its resolution."""
        [exclusion] = _exclusions(checkout, files)
        return {entry["name"]: entry["resolved"] for entry in exclusion["entries"]}

    def test_names_resolve_to_governed_fixture_or_unknown(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Packages and test targets are governed, fixture trees are not."""
        resolved = self._resolved(
            tmp_path,
            {
                "Cargo.toml": MANIFEST.format(name="my-app", extra=""),
                "tests/handler_tests.rs": "",
                "tests/heavy/main.rs": "",
                "src/bin/tool.rs": "",
                "tests/fixtures/bad/Cargo.toml": MANIFEST.format(name="bad", extra=""),
                "vendor/harness/Cargo.toml": MANIFEST.format(name="harness", extra=""),
                "dylint.toml": (
                    "[l]\n# r\nexcluded_crates = [\n"
                    '"my_app", "my-app", "handler_tests", "heavy", "tool", "bad",\n'
                    '"harness", "ghost"]\n'
                ),
            },
        )
        assert resolved == {
            "my_app": "governed",
            "my-app": "governed",
            "handler_tests": "governed",
            "heavy": "governed",
            "tool": "governed",
            "bad": "fixture",
            "harness": "fixture",
            "ghost": "unknown",
        }

    def test_a_path_dependency_alias_is_a_crate_but_a_registry_dependency_is_not(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Femtologging's `_femtologging_rs` is a renamed path dependency."""
        resolved = self._resolved(
            tmp_path,
            {
                "Cargo.toml": MANIFEST.format(
                    name="femto",
                    extra=(
                        "\n[dev-dependencies]\n"
                        '_femto = { path = ".", package = "femto" }\nserde = "1"\n'
                    ),
                ),
                "dylint.toml": '[l]\n# r\nexcluded_crates = ["_femto", "serde"]\n',
            },
        )
        assert resolved == {"_femto": "governed", "serde": "unknown"}

    def test_an_explicit_target_name_counts(self, tmp_path: pathlib.Path) -> None:
        """`[[test]] name` need not match a file."""
        resolved = self._resolved(
            tmp_path,
            {
                "Cargo.toml": MANIFEST.format(
                    name="app", extra='\n[[test]]\nname = "e2e"\npath = "t/e2e.rs"\n'
                ),
                "dylint.toml": '[l]\n# r\nexcluded_crates = ["e2e"]\n',
            },
        )
        assert resolved == {"e2e": "governed"}


class TestUnreadable:
    """Nothing is guessed."""

    def test_an_invalid_dylint_config_is_recorded(self, tmp_path: pathlib.Path) -> None:
        """The policy reports it as indeterminate."""
        _write(tmp_path, {"dylint.toml": "[l\nexcluded_crates = \n"})
        envelope = build_whitaker_exclusions_envelope(tmp_path)
        assert envelope["exclusions"] == []
        assert len(envelope["unreadable"]) == 1
        assert envelope["unreadable"][0].startswith("dylint.toml: invalid TOML")

    def test_an_exclusion_that_is_not_a_list_of_strings_is_recorded(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Its entries cannot be read, so the audit says so."""
        _write(
            tmp_path,
            {
                "Cargo.toml": MANIFEST.format(name="app", extra=""),
                "dylint.toml": '[l]\n# r\nexcluded_crates = "app"\n',
            },
        )
        envelope = build_whitaker_exclusions_envelope(tmp_path)
        assert any("not a list of strings" in item for item in envelope["unreadable"])

    def test_an_unreadable_manifest_is_recorded(self, tmp_path: pathlib.Path) -> None:
        """Crate names cannot be resolved against a manifest that did not parse."""
        _write(
            tmp_path,
            {
                "Cargo.toml": "[package\nname = \n",
                "dylint.toml": '[l]\n# r\nexcluded_crates = ["x"]\n',
            },
        )
        envelope = build_whitaker_exclusions_envelope(tmp_path)
        assert any(item.startswith("Cargo.toml") for item in envelope["unreadable"])
