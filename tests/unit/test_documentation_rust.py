"""Specify the Rust facts of the documentation-presence envelope."""

from __future__ import annotations

import typing as typ

import pytest

from concordat.rules.documentation_envelope import build_documentation_envelope
from concordat.rules.documentation_rust import leading_attributes, suppressions_in

if typ.TYPE_CHECKING:
    import pathlib

LIB_MANIFEST: typ.Final = '[package]\nname = "demo"\nversion = "0.1.0"\n'


def _write(checkout: pathlib.Path, relative: str, text: str) -> None:
    """Write *text* at *relative* under *checkout*."""
    path = checkout / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _crates(checkout: pathlib.Path) -> list[dict[str, typ.Any]]:
    """Return the Rust crate facts recorded for *checkout*."""
    return typ.cast(
        "list[dict[str, typ.Any]]",
        build_documentation_envelope(checkout)["rust"]["crates"],
    )


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("", []),
        ("#![deny(missing_docs)]\n", [("deny", False, None)]),
        ("#![forbid(missing_docs)]\n", [("forbid", False, None)]),
        (
            "//! Crate docs.\n\n/* note */\n#![warn(missing_docs, unused)]\n",
            [("warn", False, None)],
        ),
        (
            '#![expect(missing_docs, reason = "derive output, see [issue]")]\n',
            [("expect", True, None)],
        ),
        ("#![allow(missing_docs)]\n", [("allow", False, None)]),
        (
            "#![cfg_attr(not(test), deny(missing_docs))]\n",
            [("deny", False, "not(test)")],
        ),
        ("#![deny(unused)]\n#![deny(missing_docs)]\n", [("deny", False, None)]),
        ("#![deny(unused)]\n", []),
        # Past the first item an attribute is not a crate attribute.
        ("pub fn f() {}\n#![deny(missing_docs)]\n", []),
        # A comment that mentions an attribute is not one.
        (
            "// #![allow(missing_docs)]\n#![deny(missing_docs)]\n",
            [("deny", False, None)],
        ),
    ],
)
def test_root_attributes_are_read_in_order(
    source: str, expected: list[tuple[str, bool, str | None]]
) -> None:
    """Only the crate's leading inner attributes count, comments skipped."""
    found = [
        (a["kind"], a["has_reason"], a["condition"])
        for a in leading_attributes(source)[0]
    ]

    assert found == expected


def test_a_crate_with_its_own_lint_table_records_the_level(
    tmp_path: pathlib.Path,
) -> None:
    """`[lints.rust] missing_docs` is read as a string or a table."""
    _write(
        tmp_path, "Cargo.toml", LIB_MANIFEST + '[lints.rust]\nmissing_docs = "deny"\n'
    )
    _write(tmp_path, "src/lib.rs", "//! Docs.\n")

    (crate,) = _crates(tmp_path)

    assert crate["name"] == "demo"
    assert crate["manifest_level"] == "deny"
    assert crate["inherits_workspace"] is False
    assert crate["attributes"] == []


def test_a_member_inherits_the_workspace_level_only_when_it_opts_in(
    tmp_path: pathlib.Path,
) -> None:
    """`[lints] workspace = true` is the opt-in; silence inherits nothing."""
    _write(
        tmp_path,
        "Cargo.toml",
        '[workspace]\nmembers = ["a", "b"]\n'
        '[workspace.lints.rust]\nmissing_docs = { level = "deny", priority = 1 }\n',
    )
    _write(
        tmp_path, "a/Cargo.toml", '[package]\nname = "a"\n[lints]\nworkspace = true\n'
    )
    _write(tmp_path, "a/src/lib.rs", "")
    _write(tmp_path, "b/Cargo.toml", '[package]\nname = "b"\n')
    _write(tmp_path, "b/src/lib.rs", "")

    a, b = _crates(tmp_path)

    assert (a["inherits_workspace"], a["workspace_level"]) == (True, "deny")
    assert (b["inherits_workspace"], b["workspace_level"]) == (False, None)


def test_a_binary_only_package_is_not_a_library(tmp_path: pathlib.Path) -> None:
    """A package with only `src/main.rs` owes no library documentation."""
    _write(tmp_path, "Cargo.toml", LIB_MANIFEST)
    _write(tmp_path, "src/main.rs", "fn main() {}\n")

    envelope = build_documentation_envelope(tmp_path)

    assert envelope["rust"]["crates"] == []
    assert envelope["applicability"]["rust_library"] is False


def test_a_lib_table_without_a_root_file_is_carried_with_an_error(
    tmp_path: pathlib.Path,
) -> None:
    """A declared library whose root is absent is not silently compliant."""
    _write(tmp_path, "Cargo.toml", LIB_MANIFEST + '[lib]\npath = "lib/mod.rs"\n')

    (crate,) = _crates(tmp_path)

    assert crate["root"] == "lib/mod.rs"
    assert crate["root_error"] == "the library root file does not exist"


def test_fixture_vendored_and_build_trees_are_not_owned_crates(
    tmp_path: pathlib.Path,
) -> None:
    """Test data and third-party crates do not fail the rule for real ones."""
    for directory in (
        "fixtures/x",
        "vendor/y",
        "target/z",
        "examples/w",
        "templates/v",
    ):
        _write(tmp_path, f"{directory}/Cargo.toml", LIB_MANIFEST)
        _write(tmp_path, f"{directory}/src/lib.rs", "")

    assert _crates(tmp_path) == []


def test_a_malformed_manifest_is_recorded_as_an_error(tmp_path: pathlib.Path) -> None:
    """A manifest the audit cannot parse is evidence of indeterminacy."""
    _write(tmp_path, "Cargo.toml", "[package\n")

    rust = build_documentation_envelope(tmp_path)["rust"]

    assert rust["crates"] == []
    assert len(rust["errors"]) == 1
    assert rust["errors"][0].startswith("Cargo.toml: invalid TOML")


@pytest.mark.parametrize(
    ("recipe", "expected"),
    [
        ("\tcargo clippy --workspace -- -D warnings\n", [True]),
        ("\t$(CARGO) clippy --all-targets\n", [True]),
        ("\t@echo running clippy later\n", []),
        ("\tcat clippy.toml\n", []),
        ("\t$(CLIPPY_CMD) --workspace\n", [False]),
        (
            '\tRUSTFLAGS="-D warnings$(if $(X),-Zthreads=8)" $(CARGO) clippy\n',
            [True],
        ),
    ],
)
def test_clippy_runs_are_recorded_from_lint_recipes(
    tmp_path: pathlib.Path, recipe: str, expected: list[bool]
) -> None:
    """A clippy line is a run; a mention or a config file is not."""
    _write(tmp_path, "Makefile", "CARGO ?= cargo\n.PHONY: lint\nlint:\n" + recipe)

    runs = build_documentation_envelope(tmp_path)["clippy_runs"]

    assert [run["resolved"] for run in runs] == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("", []),
        ("#[allow(missing_docs)]\npub mod m;\n", [(1, "allow", False)]),
        ("\n#[expect(missing_docs)]\nfn f() {}\n", [(2, "expect", False)]),
        (
            '#![expect(missing_docs, reason = "generated")]\n',
            [(1, "expect", True)],
        ),
        ("#[cfg_attr(test, allow(missing_docs))]\nfn f() {}\n", [(1, "allow", False)]),
        ("#[allow(dead_code, missing_docs)]\nfn f() {}\n", [(1, "allow", False)]),
        ("#[allow(dead_code)]\nfn f() {}\n", []),
        ("#[deny(missing_docs)]\nmod m {}\n", []),
        ("// #[allow(missing_docs)]\nfn f() {}\n", []),
    ],
)
def test_suppressions_are_found_anywhere_in_a_file(
    source: str, expected: list[tuple[int, str, bool]]
) -> None:
    """Item-level allows and expects are found; comments and other lints are not."""
    assert suppressions_in(source) == expected


def test_the_roots_own_leading_attributes_are_not_counted_twice() -> None:
    """The crate root's attribute run is reported as an attribute, not here."""
    source = "#![allow(missing_docs)]\n#[allow(missing_docs)]\nmod m;\n"
    end = source.index("#[allow")

    assert suppressions_in(source, skip_before=end) == [(2, "allow", False)]


def test_a_suppression_in_a_module_file_is_a_fact_of_the_crate(
    tmp_path: pathlib.Path,
) -> None:
    """Library sources count; the crate's tests are other crates and do not."""
    _write(tmp_path, "Cargo.toml", LIB_MANIFEST)
    _write(tmp_path, "src/lib.rs", "#![deny(missing_docs)]\nmod m;\n")
    _write(tmp_path, "src/m.rs", "#![allow(missing_docs)]\n")
    _write(tmp_path, "tests/it.rs", "#![allow(missing_docs)]\n")

    (crate,) = _crates(tmp_path)

    assert crate["suppressions"] == [
        {"file": "src/m.rs", "line": 1, "kind": "allow", "has_reason": False}
    ]
    assert crate["source_errors"] == []


def test_an_undecodable_source_file_is_recorded(tmp_path: pathlib.Path) -> None:
    """A file the scan cannot read is evidence of indeterminacy, not absence."""
    _write(tmp_path, "Cargo.toml", LIB_MANIFEST)
    _write(tmp_path, "src/lib.rs", "")
    (tmp_path / "src" / "bad.rs").write_bytes(b"\xff\xfe")

    (crate,) = _crates(tmp_path)

    assert crate["source_errors"] == ["src/bad.rs"]
