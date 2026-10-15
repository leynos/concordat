"""Specify the Rust facts of the documentation-presence envelope."""

from __future__ import annotations

import pathlib
import re
import tempfile
import typing as typ

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from concordat.errors import OperationalRuleError
from concordat.rules import fs_probe
from concordat.rules.documentation_envelope import build_documentation_envelope
from concordat.rules.fs_probe import FileProbe
from concordat.rules.rust_source import (
    leading_attributes,
    mask_literals,
    suppressions_in,
)

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
        ("\tcargo +nightly clippy --locked\n", [True]),
        ("\tcargo --locked clippy\n", [True]),
        ("\tcargo -Z unstable-options clippy\n", [True]),
        ("\tcargo fmt --check && cargo clippy\n", [True]),
        ("\tRUSTFLAGS=-Dwarnings cargo clippy\n", [True]),
        ("\trustup run nightly cargo clippy\n", [True]),
        (
            '\tRUSTFLAGS="-D warnings$(if $(X),$(shell uname -s))" $(CARGO) clippy\n',
            [True],
        ),
        (
            (
                "\t$(or $(shell command -v cargo 2>/dev/null),$(HOME)/.cargo/bin/cargo)"
                " clippy --all-targets\n"
            ),
            [True],
        ),
        ("\t@echo running clippy later\n", []),
        ("\tcat clippy.toml\n", []),
        ("\trustup component add clippy\n", []),
        ("\t$(MAKE) clippy\n", []),
        ('\ttest -x "$$(command -v cargo-clippy)" || echo clippy missing\n', []),
        ("\tcargo build --features clippy\n", []),
        ("\t$(CLIPPY_CMD) --workspace\n", [False]),
        ("\t$(UNKNOWN) clippy\n", [False]),
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


@pytest.mark.parametrize(
    "source",
    [
        'const S: &str = "#[allow(missing_docs)]";\n',
        'const S: &str = r#"#[allow(missing_docs)]"#;\n',
        'const S: &[u8] = b"#[allow(missing_docs)]";\n',
        "/* #[allow(missing_docs)] */\nfn f() {}\n",
        "/* outer /* #[allow(missing_docs)] */ still comment */\nfn f() {}\n",
        "/// ```\n/// #[allow(missing_docs)]\n/// ```\nfn f() {}\n",
        "let c = '#'; // #[allow(missing_docs)]\n",
    ],
)
def test_attribute_text_in_comments_and_literals_is_not_a_suppression(
    source: str,
) -> None:
    """Only a real attribute counts, never text that merely contains one."""
    assert suppressions_in(source) == []


def test_a_lifetime_does_not_mask_the_rest_of_the_line() -> None:
    """`'a` has no closing quote; the attribute after it is still found."""
    source = "fn f<'a>(x: &'a str) {}\n#[allow(missing_docs)]\nmod m;\n"

    assert suppressions_in(source) == [(2, "allow", False)]


def test_masking_keeps_the_length_the_quotes_and_the_line_numbers() -> None:
    """A `reason` string stays a string, and line numbers do not move."""
    source = '/* a\nb */\n#[expect(missing_docs, reason = "why")]\nmod m;\n'

    masked = mask_literals(source)

    assert len(masked) == len(source)
    assert masked.count("\n") == source.count("\n")
    assert suppressions_in(source) == [(3, "expect", True)]


@pytest.mark.parametrize(
    "literal", ["'x'", "'#'", "'\\n'", "'\\''", "'\\u{1F600}'", "'\\x41'"]
)
def test_character_literals_are_blanked_and_lifetimes_are_not(literal: str) -> None:
    """A character literal's body is blanked; a lifetime's is not."""
    assert mask_literals(literal) == "'" + " " * (len(literal) - 2) + "'"
    assert mask_literals("&'a str") == "&'a str"


def test_a_top_level_library_root_does_not_read_the_crates_other_targets(
    tmp_path: pathlib.Path,
) -> None:
    """A top-level library root must not pull in tests and benches."""
    _write(tmp_path, "Cargo.toml", LIB_MANIFEST + '[lib]\npath = "lib.rs"\n')
    _write(tmp_path, "lib.rs", "#![deny(missing_docs)]\nmod m;\n")
    _write(tmp_path, "m.rs", "#[allow(missing_docs)]\npub fn f() {}\n")
    _write(tmp_path, "tests/it.rs", "#![allow(missing_docs)]\n")
    _write(tmp_path, "benches/b.rs", "#![allow(missing_docs)]\n")

    (crate,) = _crates(tmp_path)

    assert [item["file"] for item in crate["suppressions"]] == ["m.rs"]


def test_a_source_directory_the_filesystem_refuses_to_describe_is_an_error(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A refused probe is an audit failure, never a library with no sources."""
    _write(tmp_path, "Cargo.toml", LIB_MANIFEST)
    _write(tmp_path, "src/lib.rs", "")
    monkeypatch.setattr(
        fs_probe,
        "probe_dir",
        lambda _path: FileProbe(present=False, read_error="permission denied"),
    )

    with pytest.raises(OperationalRuleError, match="permission denied"):
        _crates(tmp_path)


_RUST_PIECES: typ.Final = st.sampled_from([
    "#[allow(missing_docs)]",
    "/* ",
    " */",
    "/*",
    "//",
    '"',
    'r#"',
    '"#',
    "'a",
    "'x'",
    "'\\n'",
    "\\",
    "\n",
    " ",
    "fn f() {}",
    "#![deny(missing_docs)]",
    "(",
    ")",
    "[",
    "]",
])


@given(st.lists(_RUST_PIECES, max_size=30).map("".join))
def test_masking_preserves_length_and_line_structure(source: str) -> None:
    """Whatever the input, masking never moves a byte or a line."""
    masked = mask_literals(source)

    assert len(masked) == len(source)
    assert [i for i, c in enumerate(masked) if c == "\n"] == [
        i for i, c in enumerate(source) if c == "\n"
    ]


@given(st.lists(_RUST_PIECES, max_size=30).map("".join))
def test_masking_never_adds_attributes(source: str) -> None:
    """Masking only blanks text, so it cannot create an attribute.

    It is not idempotent on arbitrary text: two adjacent character literals
    share a quote (`'x''y'`), and once the first is blanked the second pass can
    read the shared quote as an opener. Real Rust has no such sequence.
    """
    masked = mask_literals(source)

    assert masked.count("#[") <= source.count("#[")
    assert masked.count("#![") <= source.count("#![")


def test_code_after_a_raw_string_is_still_scanned() -> None:
    """The closing delimiter ends the raw string; what follows is source again."""
    source = 'const S: &str = r#"x"#;\n#[allow(missing_docs)]\nmod m;\n'

    assert suppressions_in(source) == [(2, "allow", False)]


_REAL_SUPPRESSIONS: typ.Final = {
    "#[allow(missing_docs)]\n": ("allow", False),
    "#![allow(missing_docs)]\n": ("allow", False),
    "#[expect(missing_docs)]\n": ("expect", False),
    '#[expect(missing_docs, reason = "generated")]\n': ("expect", True),
    "#[cfg_attr(test, allow(missing_docs))]\n": ("allow", False),
}
_DECOYS: typ.Final = (
    "// #[allow(missing_docs)]\n",
    "/* #[allow(missing_docs)] */\n",
    'const S: &str = "#[allow(missing_docs)]";\n',
    'const R: &str = r#"#[allow(missing_docs)]"#;\n',
    "/// #[expect(missing_docs)]\n",
    "fn f<'a>(x: &'a str) {}\n",
    "let c = '#';\n",
    "#[allow(dead_code)]\n",
)


@given(
    st.lists(
        st.one_of(
            st.sampled_from(sorted(_REAL_SUPPRESSIONS)),
            st.sampled_from(_DECOYS),
        ),
        max_size=12,
    )
)
def test_suppressions_are_exactly_the_real_attributes(lines: list[str]) -> None:
    """Whatever surrounds them, only real attributes are reported, on their lines."""
    expected = [
        (number, *_REAL_SUPPRESSIONS[line])
        for number, line in enumerate(lines, start=1)
        if line in _REAL_SUPPRESSIONS
    ]

    assert suppressions_in("".join(lines)) == expected


_CARGO_PREFIXES: typ.Final = (
    "cargo",
    "$(CARGO)",
    "RUSTFLAGS=-Dwarnings cargo",
    "env FOO=1 cargo",
    "rustup run nightly cargo",
    "cargo +nightly",
)
_CLIPPY_REAL: typ.Final = st.builds(
    lambda prefix, flags: f"{prefix} {flags} clippy --workspace".replace("  ", " "),
    st.sampled_from(_CARGO_PREFIXES),
    st.sampled_from(["", "--locked", "-Z unstable-options"]),
)
_CLIPPY_DECOY: typ.Final = st.sampled_from([
    "echo cargo clippy",
    "rustup component add clippy",
    "touch clippy",
    "cargo build --features clippy",
    "cargo fmt --check",
    'test -x "$$(command -v cargo-clippy)"',
    "make clippy",
])


# Real parser subprocess and filesystem work are not a performance contract.
@settings(deadline=None)
@given(st.lists(st.one_of(_CLIPPY_REAL, _CLIPPY_DECOY), min_size=1, max_size=6))
def test_clippy_runs_are_exactly_the_cargo_clippy_commands(commands: list[str]) -> None:
    """Each recipe line is a run if and only if it is a real `cargo clippy`."""
    expected = [
        bool(re.search(r"(^| )clippy --workspace$", command)) for command in commands
    ]
    makefile = "CARGO ?= cargo\n.PHONY: lint\nlint:\n" + "".join(
        f"\t{command}\n" for command in commands
    )
    with tempfile.TemporaryDirectory() as tmp:
        _write(pathlib.Path(tmp), "Makefile", makefile)
        runs = build_documentation_envelope(pathlib.Path(tmp))["clippy_runs"]

    assert [run["resolved"] for run in runs] == [flag for flag in expected if flag]
