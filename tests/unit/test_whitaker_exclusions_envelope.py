"""Unit tests for the `policy-input/whitaker-exclusions` envelope builder.

The Rego suite proves what the policy makes of each envelope; these tests prove
what the builder records: where each exclusion is, whether a comment states its
reason, how a crate name resolves, and what it refuses to guess.
"""

from __future__ import annotations

import typing as typ

import pytest

from concordat.errors import OperationalRuleError
from concordat.rules import whitaker_exclusions_envelope
from concordat.rules.whitaker_exclusions_envelope import (
    ENVELOPE_KIND,
    build_whitaker_exclusions_envelope,
)
from concordat.rules.whitaker_exclusions_text import locate_exclusions

if typ.TYPE_CHECKING:
    import pathlib

MANIFEST: typ.Final = '[package]\nname = "{name}"\nversion = "0.1.0"\n{extra}'


class LinkCase(typ.NamedTuple):
    """A symbolic link to lay in a checkout, and the note it must raise."""

    files: dict[str, str]
    link: str
    target: str
    note: str


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


class TestMultilineStrings:
    """A line inside a multi-line string is content, not structure."""

    def test_a_quoted_key_and_comment_do_not_become_a_real_keys_reason(self) -> None:
        """CodeRabbit's example: the string holds both a comment and a key."""
        text = (
            '[t]\nexcluded_paths = ["loader"]\nnote = """\n'
            '# not a reason\nexcluded_paths = ["bogus"]\n"""\n'
        )
        found = locate_exclusions(text)
        assert [(f.key, f.line, f.has_reason) for f in found] == [
            ("excluded_paths", 2, False)
        ]

    @pytest.mark.parametrize(
        "text",
        [
            "[t]\nnote = '''\n[fake]\nexcluded_paths = [\"x\"]\n'''\n",
            '[t]\nnote = """\n[fake]\nexcluded_paths = ["x"]\n"""\n',
            '[t]\nnote = """a \\\nexcluded_paths = ["x"]\nb"""\n',
        ],
    )
    def test_a_header_or_key_inside_a_string_is_not_found(self, text: str) -> None:
        """Whichever quote opens the string, and across an escaped line break."""
        assert locate_exclusions(text) == []

    def test_a_key_after_the_string_ends_is_found(self) -> None:
        """The scan leaves the string at its closing quotes."""
        text = '[t]\nnote = """\nx\n"""\n# why\nexcluded_paths = ["a"]\n'
        [found] = locate_exclusions(text)
        assert (found.line, found.has_reason) == (6, True)

    def test_a_comment_line_inside_a_string_is_not_a_reason(self) -> None:
        """The block above the key stops at string content."""
        text = '[t]\nnote = """\n# inside\n"""\nexcluded_paths = ["a"]\n'
        [found] = locate_exclusions(text)
        assert found.has_reason is False

    def test_quotes_and_hashes_in_single_line_strings_are_not_structure(self) -> None:
        """A `#` in a string is not a comment, and a lone quote ends at the line."""
        text = 'a = "x # y"\nb = \'it\'\n[t]\n# why\nexcluded_paths = ["a"]\n'
        [found] = locate_exclusions(text)
        assert (found.table, found.line, found.has_reason) == ("t", 5, True)


class TestEnvelope:
    """What the builder records for a checkout."""

    def test_a_repeated_table_header_leaves_its_key_not_located(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Two `[[rule]]` tables each with the key: the line is ambiguous."""
        found = _exclusions(
            tmp_path,
            {
                "dylint.toml": (
                    '[[rule]]\n# why\nexcluded_paths = ["a"]\n\n'
                    '[[rule]]\nexcluded_paths = ["b"]\n'
                )
            },
        )
        assert [(x["table"], x["line"]) for x in found] == [
            ("rule", None),
            ("rule", None),
        ]

    def test_a_symlinked_dylint_config_is_recorded_not_followed(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Its exclusions are unknown, which is not the same as none."""
        _write(tmp_path, {"real.toml": '[l]\n# why\nexcluded_paths = ["a"]\n'})
        (tmp_path / "dylint.toml").symlink_to("real.toml")
        envelope = build_whitaker_exclusions_envelope(tmp_path)
        assert envelope["exclusions"] == []
        [note] = envelope["unreadable"]
        assert note.startswith("dylint.toml: is a symbolic link")

    def test_a_dylint_config_that_cannot_be_opened_is_recorded(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A read failure inside the checkout is EN-001, not a failed audit."""
        _write(tmp_path, {"dylint.toml": '[l]\nexcluded_paths = ["a"]\n'})

        def refuse(_path: pathlib.Path, operation: str) -> str:
            """Fail the way a file that cannot be opened does."""
            message = "permission denied"
            raise OperationalRuleError(message, operation=operation)

        monkeypatch.setattr(whitaker_exclusions_envelope, "read_text", refuse)
        envelope = build_whitaker_exclusions_envelope(tmp_path)
        assert envelope["exclusions"] == []
        assert envelope["unreadable"][0].startswith("dylint.toml: ")
        assert "permission denied" in envelope["unreadable"][0]

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

    def test_target_specific_and_workspace_inherited_path_aliases_count(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A path dependency is local wherever it is declared."""
        resolved = self._resolved(
            tmp_path,
            {
                "Cargo.toml": (
                    MANIFEST.format(
                        name="app",
                        extra=(
                            "\n[target.'cfg(unix)'.dev-dependencies]\n"
                            '_app = { path = ".", package = "app" }\n'
                            "[dependencies]\nshared_alias = { workspace = true }\n"
                            "registry = { workspace = true }\n\n[workspace]\n"
                            "[workspace.dependencies]\n"
                            'shared_alias = { path = "shared", package = "shared" }\n'
                            'registry = "1"\n'
                        ),
                    )
                ),
                "shared/Cargo.toml": MANIFEST.format(name="shared", extra=""),
                "dylint.toml": (
                    '[l]\n# r\nexcluded_crates = ["_app", "shared_alias", "registry"]\n'
                ),
            },
        )
        assert resolved == {
            "_app": "governed",
            "shared_alias": "governed",
            "registry": "unknown",
        }

    @pytest.mark.parametrize(
        ("switch", "folder"),
        [
            ("autotests", "tests"),
            ("autobenches", "benches"),
            ("autoexamples", "examples"),
            ("autobins", "src/bin"),
        ],
    )
    def test_a_switched_off_discovery_leaves_only_declared_targets(
        self, tmp_path: pathlib.Path, switch: str, folder: str
    ) -> None:
        """`autotests = false` and its siblings turn that folder's discovery off."""
        resolved = self._resolved(
            tmp_path,
            {
                "Cargo.toml": MANIFEST.format(name="app", extra=f"{switch} = false\n"),
                f"{folder}/old.rs": "",
                "dylint.toml": '[l]\n# r\nexcluded_crates = ["old"]\n',
            },
        )
        assert resolved == {"old": "unknown"}

    def test_a_workspace_root_the_scan_cannot_inspect_leaves_the_aliases_unknown(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A named root outside the checkout is not guessed from an ancestor."""
        checkout = tmp_path / "checkout"
        _write(
            checkout,
            {
                "Cargo.toml": '[workspace]\nmembers = ["app"]\n'
                '[workspace.dependencies]\nshared_alias = { path = "shared" }\n',
                "shared/Cargo.toml": MANIFEST.format(name="shared", extra=""),
                "app/Cargo.toml": MANIFEST.format(
                    name="app",
                    extra=(
                        'workspace = "../../elsewhere"\n\n[dependencies]\n'
                        "shared_alias = { workspace = true }\n"
                    ),
                ),
                "dylint.toml": '[l]\n# r\nexcluded_crates = ["shared_alias"]\n',
            },
        )
        envelope = build_whitaker_exclusions_envelope(checkout)
        [exclusion] = envelope["exclusions"]
        assert exclusion["entries"] == [{"name": "shared_alias", "resolved": "unknown"}]
        assert any("cannot inspect" in note for note in envelope["unreadable"])

    def test_a_file_a_declared_target_names_by_path_infers_no_second_target(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Cargo merges targets by path: `current` owns `tests/old.rs`, so no `old`."""
        resolved = self._resolved(
            tmp_path,
            {
                "Cargo.toml": MANIFEST.format(
                    name="app",
                    extra='[[test]]\nname = "current"\npath = "tests/old.rs"\n',
                ),
                "tests/old.rs": "",
                "tests/other.rs": "",
                "dylint.toml": (
                    '[l]\n# r\nexcluded_crates = ["current", "old", "other"]\n'
                ),
            },
        )
        assert resolved == {
            "current": "governed",
            "old": "unknown",
            "other": "governed",
        }

    def test_a_test_directory_is_named_after_itself_not_its_main(
        self, tmp_path: pathlib.Path
    ) -> None:
        """`tests/group/main.rs` is the target `group`; `main` is nothing."""
        resolved = self._resolved(
            tmp_path,
            {
                "Cargo.toml": MANIFEST.format(name="app", extra=""),
                "tests/group/main.rs": "",
                "dylint.toml": '[l]\n# r\nexcluded_crates = ["group", "main"]\n',
            },
        )
        assert resolved == {"group": "governed", "main": "unknown"}

    def test_a_declared_target_counts_even_when_discovery_is_off(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Only auto-discovery is switched off."""
        resolved = self._resolved(
            tmp_path,
            {
                "Cargo.toml": MANIFEST.format(
                    name="app",
                    extra=(
                        "autotests = false\n[[test]]\n"
                        'name = "kept"\npath = "tests/k.rs"\n'
                    ),
                ),
                "dylint.toml": '[l]\n# r\nexcluded_crates = ["kept"]\n',
            },
        )
        assert resolved == {"kept": "governed"}

    @pytest.mark.parametrize(
        "case",
        [
            LinkCase({}, "tests", "../host", "tests: is a symbolic link"),
            LinkCase({}, "src", "../host", "src: is a symbolic link"),
            LinkCase({}, "tests", "nowhere", "tests: is a symbolic link"),
            LinkCase(
                {"src/bin/secret/placeholder": ""},
                "src/bin/secret/main.rs",
                "../../../../host/secret.rs",
                "main.rs: is a symbolic",
            ),
            LinkCase(
                {"real/sub/dylint.toml": ""}, "linked", "real", "linked: is a symbolic"
            ),
        ],
    )
    def test_a_link_anywhere_a_name_could_come_from_is_recorded_not_followed(
        self, tmp_path: pathlib.Path, case: LinkCase
    ) -> None:
        """A link to the host, to nothing, or to a probe is never read as absence.

        The name `secret` is only on the far side of a link; it must stay
        unresolved, and the link must be reported.
        """
        (tmp_path / "host" / "bin").mkdir(parents=True)
        (tmp_path / "host" / "secret.rs").write_text("", encoding="utf-8")
        (tmp_path / "host" / "bin" / "secret.rs").write_text("", encoding="utf-8")
        checkout = tmp_path / "checkout"
        _write(
            checkout,
            {
                "Cargo.toml": MANIFEST.format(name="app", extra=""),
                "dylint.toml": '[l]\n# r\nexcluded_crates = ["secret"]\n',
                **case.files,
            },
        )
        (checkout / case.link).parent.mkdir(parents=True, exist_ok=True)
        (checkout / case.link).symlink_to(case.target)
        envelope = build_whitaker_exclusions_envelope(checkout)
        [exclusion] = envelope["exclusions"]
        assert exclusion["entries"] == [{"name": "secret", "resolved": "unknown"}]
        assert any(case.note in n for n in envelope["unreadable"]), envelope[
            "unreadable"
        ]

    @pytest.mark.parametrize(
        ("other_workspace", "expected"),
        [
            # Workspace `b` declares `shared` as a path dependency and a member
            # uses it, so the name is a crate of the repository.
            ("used", "governed"),
            # `b` declares it but no member uses it: the declaration is not a
            # crate, and must not be attributed to `a`'s registry dependency.
            ("unused", "unknown"),
            # Alone, workspace `a` says registry, so the name is not a crate.
            ("absent", "unknown"),
        ],
    )
    def test_an_inherited_dependency_is_resolved_in_the_workspace_that_owns_it(
        self, tmp_path: pathlib.Path, other_workspace: str, expected: str
    ) -> None:
        """One workspace's registry dependency is not made local by another's."""
        inherit = "[dependencies]\nshared = { workspace = true }\n"
        files = {
            "a/Cargo.toml": "[workspace]\nmembers = ['m']\n"
            '[workspace.dependencies]\nshared = "1"\n',
            "a/m/Cargo.toml": MANIFEST.format(name="m", extra=inherit),
            "dylint.toml": '[l]\n# r\nexcluded_crates = ["shared"]\n',
        }
        if other_workspace != "absent":
            files["b/Cargo.toml"] = (
                '[workspace]\nmembers = ["n"]\n'
                '[workspace.dependencies]\nshared = { path = "n" }\n'
            )
            files["b/n/Cargo.toml"] = MANIFEST.format(
                name="n", extra=inherit if other_workspace == "used" else ""
            )
        _write(tmp_path, files)
        [exclusion] = build_whitaker_exclusions_envelope(tmp_path)["exclusions"]
        assert exclusion["entries"] == [{"name": "shared", "resolved": expected}]

    def test_a_package_that_names_a_workspace_that_is_not_an_ancestor_inherits_from_it(
        self, tmp_path: pathlib.Path
    ) -> None:
        """`package.workspace = "../ws"` selects the owner, wherever it is."""
        _write(
            tmp_path,
            {
                "ws/Cargo.toml": "[workspace]\nmembers = []\n"
                '[workspace.dependencies]\nshared_alias = { path = "../shared" }\n',
                "shared/Cargo.toml": MANIFEST.format(name="shared", extra=""),
                "app/Cargo.toml": MANIFEST.format(
                    name="app",
                    extra=(
                        'workspace = "../ws"\n\n[dependencies]\n'
                        "shared_alias = { workspace = true }\n"
                    ),
                ),
                "dylint.toml": '[l]\n# r\nexcluded_crates = ["shared_alias"]\n',
            },
        )
        [exclusion] = build_whitaker_exclusions_envelope(tmp_path)["exclusions"]
        assert exclusion["entries"] == [
            {"name": "shared_alias", "resolved": "governed"}
        ]

    def test_a_symlinked_entry_in_a_target_folder_is_not_followed(
        self, tmp_path: pathlib.Path
    ) -> None:
        """One linked file hides one name; the others still count."""
        checkout = tmp_path / "checkout"
        _write(
            checkout,
            {
                "Cargo.toml": MANIFEST.format(name="app", extra=""),
                "tests/real.rs": "",
                "dylint.toml": '[l]\n# r\nexcluded_crates = ["real", "linked"]\n',
            },
        )
        (checkout / "tests" / "linked.rs").symlink_to("real.rs")
        envelope = build_whitaker_exclusions_envelope(checkout)
        [exclusion] = envelope["exclusions"]
        assert {e["name"]: e["resolved"] for e in exclusion["entries"]} == {
            "real": "governed",
            "linked": "unknown",
        }
        assert any("linked.rs" in n for n in envelope["unreadable"])

    def test_a_symlinked_manifest_is_recorded(self, tmp_path: pathlib.Path) -> None:
        """A manifest that is a link would hide its crate names."""
        _write(
            tmp_path,
            {
                "real/Cargo.toml": MANIFEST.format(name="real", extra=""),
                "dylint.toml": '[l]\n# r\nexcluded_crates = ["real"]\n',
            },
        )
        (tmp_path / "Cargo.toml").symlink_to("real/Cargo.toml")
        envelope = build_whitaker_exclusions_envelope(tmp_path)
        assert any(
            n.startswith("Cargo.toml: is a symbolic link")
            for n in envelope["unreadable"]
        )

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
