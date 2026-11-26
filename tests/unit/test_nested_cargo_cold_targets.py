"""Pin the test binaries a nested build records for a `--test NAME` warming run.

A warming run limited to `--test NAME` warms only the nested builds that test
binary compiles, so each build records the `tests/<name>.rs` binaries whose
module tree reaches its file. The record is unknown (`None`) when it could not
be complete: nothing seen reaching the file, an owner the resolver cannot see,
or a manifest that names its test targets itself.
"""

from __future__ import annotations

import typing as typ

import pytest

from concordat.rules.nested_cargo_cold_envelope import build_nested_cargo_cold_envelope

if typ.TYPE_CHECKING:
    import pathlib

SUPPORT_BUILD = """\
pub fn build() {
    let t = trybuild::TestCases::new();
    t.pass("x.rs");
}
"""
TRYBUILD_TEST = """\
#[test]
fn step_macros_compile() {
    let t = trybuild::TestCases::new();
    t.pass("tests/fixtures/ok.rs");
}
"""


def checkout(root: pathlib.Path, files: dict[str, str]) -> None:
    """Write a package named `app`, plus *files*, creating their directories."""
    files = {"Cargo.toml": '[package]\nname = "app"\n', **files}
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def targets(root: pathlib.Path) -> dict[str, list[str] | None]:
    """Return each nested build's path with the test binaries that compile it."""
    builds = build_nested_cargo_cold_envelope(root)["builds"]
    return {build["path"]: build["targets"] for build in builds}


def test_a_support_build_records_the_test_binaries_that_compile_it(
    tmp_path: pathlib.Path,
) -> None:
    """A warming `--test NAME` run is compared with the binaries that reach a file."""
    checkout(
        tmp_path,
        {
            "tests/ui.rs": "mod support;\n",
            "tests/other.rs": "mod support;\n",
            "tests/support/mod.rs": SUPPORT_BUILD,
        },
    )
    assert targets(tmp_path) == {"tests/support/mod.rs": ["other", "ui"]}


def test_a_directory_test_target_names_the_modules_it_imports(
    tmp_path: pathlib.Path,
) -> None:
    """`tests/ui/main.rs` is target `ui`, and the support module it declares is ui's."""
    checkout(
        tmp_path,
        {"tests/ui/main.rs": "mod support;\n", "tests/ui/support.rs": SUPPORT_BUILD},
    )
    assert targets(tmp_path) == {"tests/ui/support.rs": ["ui"]}


def test_a_test_table_names_the_target_of_its_own_path(
    tmp_path: pathlib.Path,
) -> None:
    """`[[test]] name = ...` can give `tests/ui.rs` another name than its file's."""
    checkout(
        tmp_path,
        {
            "Cargo.toml": (
                '[package]\nname = "app"\nautotests = false\n\n'
                '[[test]]\nname = "contract"\npath = "tests/ui.rs"\n\n'
                '[[test]]\nname = "ui"\npath = "tests/other.rs"\n'
            ),
            "tests/ui.rs": TRYBUILD_TEST,
            "tests/other.rs": "fn other() {}\n",
        },
    )
    assert targets(tmp_path) == {"tests/ui.rs": ["contract"]}


@pytest.mark.parametrize(
    ("tables", "source", "expected"),
    [
        (
            (
                'name = "a"\npath = "tests/ui.rs"\n\n'
                '[[test]]\nname = "b"\npath = "tests/ui.rs"\n'
            ),
            "tests/ui.rs",
            ["a", "b"],
        ),
        ('name = "a"\npath = "tests/../tests/ui.rs"\n', "tests/ui.rs", ["a"]),
        ('name = "ui"\n', "tests/ui/main.rs", ["ui"]),
    ],
)
def test_test_tables_share_normalise_and_default_their_paths(
    tmp_path: pathlib.Path, tables: str, source: str, expected: list[str]
) -> None:
    """Each table is a target of its own; paths are normalised; no path is a default."""
    checkout(
        tmp_path,
        {
            "Cargo.toml": (
                f'[package]\nname = "app"\nautotests = false\n\n[[test]]\n{tables}'
            ),
            source: TRYBUILD_TEST,
        },
    )
    assert targets(tmp_path) == {source: expected}


def test_a_file_no_test_table_names_stays_unknown_in_a_named_crate(
    tmp_path: pathlib.Path,
) -> None:
    """A file the manifest does not name may run under any target's name."""
    checkout(
        tmp_path,
        {
            "Cargo.toml": (
                '[package]\nname = "app"\nautotests = false\n\n'
                '[[test]]\nname = "other"\npath = "tests/other.rs"\n'
            ),
            "tests/ui.rs": TRYBUILD_TEST,
            "tests/other.rs": "fn other() {}\n",
        },
    )
    assert targets(tmp_path) == {"tests/ui.rs": None}


def test_an_owner_the_resolver_cannot_see_leaves_the_targets_unknown(
    tmp_path: pathlib.Path,
) -> None:
    """A file another package `include!`s is compiled by more than the seen binaries."""
    checkout(
        tmp_path,
        {
            "a/Cargo.toml": '[package]\nname = "a"\n',
            "b/Cargo.toml": '[package]\nname = "b"\n',
            "a/tests/ui.rs": "mod support;\n",
            "a/tests/support/mod.rs": SUPPORT_BUILD,
            "b/tests/other.rs": 'include!("../../a/tests/support/mod.rs");\n',
        },
    )
    assert targets(tmp_path)["a/tests/support/mod.rs"] is None
