"""A Cargo manifest with no Rust source is not something the build rules judge.

lading carries a root `Cargo.toml` and no `.rs` file, so the footprint, build
defaults and Makefile rules found a Rust build that is not there. The command
boundary now reads such a checkout as not applicable.
"""

from __future__ import annotations

import typing as typ

import pytest

from concordat.rules import packages, rust_sources
from concordat.rules.rust_sources import has_rust_sources

if typ.TYPE_CHECKING:
    import pathlib


@pytest.fixture(name="rust_sources_are_assumed", autouse=True)
def fixture_rust_sources_are_assumed() -> None:
    """Use the real source check here, not the suite-wide assumption."""


MANIFEST = '[package]\nname = "x"\nversion = "0.1.0"\n\n[profile.dev]\ndebug = 2\n'


def _checkout(root: pathlib.Path, *, with_source: bool) -> None:
    (root / "Cargo.toml").write_text(MANIFEST, encoding="utf-8")
    if with_source:
        (root / "src").mkdir()
        (root / "src" / "lib.rs").write_text("", encoding="utf-8")


def test_a_checkout_with_a_rust_file_has_rust_sources(tmp_path: pathlib.Path) -> None:
    """A Rust file below the root is Rust source."""
    _checkout(tmp_path, with_source=True)

    assert has_rust_sources(tmp_path)


def test_a_manifest_alone_has_no_rust_sources(tmp_path: pathlib.Path) -> None:
    """A manifest alone is not Rust source."""
    _checkout(tmp_path, with_source=False)

    assert not has_rust_sources(tmp_path)


def test_a_rust_file_under_a_hidden_directory_does_not_count(
    tmp_path: pathlib.Path,
) -> None:
    """Hidden directories are not entered, so a stray file there is ignored."""
    _checkout(tmp_path, with_source=False)
    (tmp_path / ".cache").mkdir()
    (tmp_path / ".cache" / "stray.rs").write_text("", encoding="utf-8")

    assert not has_rust_sources(tmp_path)


def test_an_incomplete_scan_does_not_claim_there_is_no_rust(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A directory the walk cannot list may hold the only Rust files."""
    _checkout(tmp_path, with_source=False)

    def incomplete(_checkout: pathlib.Path, problems: list[str]) -> list[pathlib.Path]:
        problems.append("cannot list a directory")
        return []

    monkeypatch.setattr(rust_sources, "walk_files", incomplete)

    assert has_rust_sources(tmp_path)


@pytest.mark.parametrize("with_source", [False, True])
def test_the_build_rules_see_surfaces_only_where_there_is_rust(
    tmp_path: pathlib.Path, *, with_source: bool
) -> None:
    """The three Rust rules empty their surfaces for a Cargo-only checkout."""
    _checkout(tmp_path, with_source=with_source)

    defaults = packages._build_defaults_envelope(tmp_path)
    makefile = packages._makefile_envelope(tmp_path)
    footprint = packages._dev_build_envelope(tmp_path)

    assert bool(defaults["cargo"]["surfaces"]) is with_source
    assert bool(makefile["cargo"]["surfaces"]) is with_source
    assert bool(footprint["surfaces"]) is with_source
    flag = defaults["applicability"].get("rust_sources")
    assert flag is (None if with_source else False)
