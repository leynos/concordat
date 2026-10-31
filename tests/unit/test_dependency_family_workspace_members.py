"""Specify how a governed workspace root brings its member crates into view.

A Cargo workspace lists its member crates in `[workspace] members`, and each
member declares its own dependencies. A repository that governs the workspace
root governs the whole workspace, so the dependency-family facts read the
members too. They are read for dependency facts only: they are not Rust
surfaces, because other rules judge a surface as a project root and a member
manifest is not one. A manifest the workspace does not list stays outside the
audit and says so.
"""

from __future__ import annotations

import typing as typ

import pytest

from concordat.rules.dependency_family_envelope import build_dependency_family_envelope

if typ.TYPE_CHECKING:
    import pathlib

_CONFIG: typ.Final = """\
version: 2
updates:
  - package-ecosystem: cargo
    directory: /
    schedule:
      interval: daily
    groups:
      minor-and-patch:
        patterns: ['*']
        update-types: [minor, patch]
"""


def _member(*, name: str = "native", dependencies: str = 'pyo3 = "0.28.3"') -> str:
    """Return a member manifest with the given dependency lines."""
    return (
        f'[package]\nname = "{name}"\nversion = "0.1.0"\n\n'
        f"[dependencies]\n{dependencies}\n"
    )


def _workspace(members: str, extra: str = "") -> str:
    """Return a root workspace manifest listing *members* (TOML array text)."""
    return f"[workspace]\nmembers = {members}\n{extra}"


def _write(checkout: pathlib.Path, relative: str, text: str) -> None:
    """Write *text* at *relative* under *checkout*."""
    path = checkout / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _manifests(checkout: pathlib.Path) -> set[str]:
    """Return the manifests the envelope read dependency facts from."""
    envelope = build_dependency_family_envelope(checkout)
    return {declaration["manifest"] for declaration in envelope["declarations"]}


@pytest.fixture
def repository(tmp_path: pathlib.Path) -> pathlib.Path:
    """Provide a Dependabot configuration and nothing else."""
    _write(tmp_path, ".github/dependabot.yml", _CONFIG)
    return tmp_path


def test_a_listed_member_is_read_and_is_not_a_problem(
    repository: pathlib.Path,
) -> None:
    """The root workspace names `native`, so its PyO3 edge is a declaration."""
    _write(repository, "Cargo.toml", _workspace('["native"]'))
    _write(repository, "native/Cargo.toml", _member())

    envelope = build_dependency_family_envelope(repository)

    assert envelope["problems"] == []
    assert [d["manifest"] for d in envelope["declarations"]] == ["native/Cargo.toml"]


def test_a_glob_member_pattern_reaches_every_matching_crate(
    repository: pathlib.Path,
) -> None:
    """`crates/*` lists each crate directory that holds a manifest."""
    _write(repository, "Cargo.toml", _workspace('["crates/*"]'))
    _write(repository, "crates/a/Cargo.toml", _member(name="a"))
    _write(repository, "crates/b/Cargo.toml", _member(name="b"))
    _write(repository, "crates/notes/README.md", "no manifest here")

    assert _manifests(repository) == {"crates/a/Cargo.toml", "crates/b/Cargo.toml"}


def test_an_excluded_member_is_left_out_and_still_reported(
    repository: pathlib.Path,
) -> None:
    """An `exclude` entry takes a crate out of the workspace, so it is ungoverned."""
    _write(
        repository, "Cargo.toml", _workspace('["crates/*"]', 'exclude = ["crates/b"]\n')
    )
    _write(repository, "crates/a/Cargo.toml", _member(name="a"))
    _write(repository, "crates/b/Cargo.toml", _member(name="b"))

    envelope = build_dependency_family_envelope(repository)

    assert {d["manifest"] for d in envelope["declarations"]} == {"crates/a/Cargo.toml"}
    assert [p for p in envelope["problems"] if p.startswith("crates/b/Cargo.toml")]


def test_an_unlisted_manifest_stays_outside_the_audit(
    repository: pathlib.Path,
) -> None:
    """A crate the workspace does not list is named, not silently read."""
    _write(repository, "Cargo.toml", _workspace('["native"]'))
    _write(repository, "native/Cargo.toml", _member())
    _write(repository, "stray/Cargo.toml", _member(name="stray"))

    envelope = build_dependency_family_envelope(repository)

    assert {d["manifest"] for d in envelope["declarations"]} == {"native/Cargo.toml"}
    assert [p for p in envelope["problems"] if p.startswith("stray/Cargo.toml")]


def test_a_declared_workspace_root_brings_its_members_too(
    repository: pathlib.Path,
) -> None:
    """The declared-surface path reads members as well as the undeclared one."""
    _write(
        repository,
        ".concordat",
        "language:\n  rust:\n    surfaces:\n      - path: rust/Cargo.toml\n",
    )
    _write(repository, "rust/Cargo.toml", _workspace('["native"]'))
    _write(repository, "rust/native/Cargo.toml", _member())

    envelope = build_dependency_family_envelope(repository)

    assert envelope["problems"] == []
    assert [d["manifest"] for d in envelope["declarations"]] == [
        "rust/native/Cargo.toml"
    ]


def test_a_member_inherits_the_roots_workspace_dependencies(
    repository: pathlib.Path,
) -> None:
    """`pyo3 = { workspace = true }` takes the root's requirement."""
    _write(
        repository,
        "Cargo.toml",
        _workspace('["native"]', '\n[workspace.dependencies]\npyo3 = "0.28.3"\n'),
    )
    _write(
        repository,
        "native/Cargo.toml",
        _member(dependencies="pyo3 = { workspace = true }"),
    )

    (declaration,) = build_dependency_family_envelope(repository)["declarations"]

    assert (declaration["manifest"], declaration["requirement"]) == (
        "native/Cargo.toml",
        "0.28.3",
    )


def test_an_unparsable_member_fails_closed(repository: pathlib.Path) -> None:
    """A member that cannot be decoded is a problem, never read as no edges."""
    _write(repository, "Cargo.toml", _workspace('["native"]'))
    _write(repository, "native/Cargo.toml", "[package\n")

    envelope = build_dependency_family_envelope(repository)

    assert envelope["declarations"] == []
    assert [p for p in envelope["problems"] if p.startswith("native/Cargo.toml")]


@pytest.mark.parametrize("pattern", ["../outside", "/etc", "a/../../b"])
def test_a_member_pattern_leaving_the_checkout_is_never_followed(
    tmp_path: pathlib.Path, pattern: str
) -> None:
    """An untrusted `members` entry cannot reach outside the repository."""
    checkout = tmp_path / "checkout"
    _write(checkout, ".github/dependabot.yml", _CONFIG)
    _write(checkout, "Cargo.toml", _workspace(f'["{pattern}"]'))
    _write(tmp_path, "outside/Cargo.toml", _member(name="outside"))

    assert _manifests(checkout) == set()


def test_a_symbolic_link_member_is_refused(tmp_path: pathlib.Path) -> None:
    """A link could lead outside the checkout, so it is not followed."""
    checkout = tmp_path / "checkout"
    _write(checkout, ".github/dependabot.yml", _CONFIG)
    _write(checkout, "Cargo.toml", _workspace('["native"]'))
    _write(tmp_path, "outside/Cargo.toml", _member(name="outside"))
    (checkout / "native").symlink_to(tmp_path / "outside", target_is_directory=True)

    envelope = build_dependency_family_envelope(checkout)

    assert envelope["declarations"] == []


def test_a_nested_workspace_inside_a_member_is_not_expanded(
    repository: pathlib.Path,
) -> None:
    """Only governed roots list members; a nested workspace is still ungoverned."""
    _write(repository, "Cargo.toml", _workspace('["native"]'))
    _write(repository, "native/Cargo.toml", _member())
    _write(repository, "other/Cargo.toml", _workspace('["inner"]'))
    _write(repository, "other/inner/Cargo.toml", _member(name="inner"))

    envelope = build_dependency_family_envelope(repository)

    assert {d["manifest"] for d in envelope["declarations"]} == {"native/Cargo.toml"}
    assert [p for p in envelope["problems"] if p.startswith("other/inner/Cargo.toml")]


def test_a_member_that_is_also_a_declared_surface_is_read_once(
    repository: pathlib.Path,
) -> None:
    """A repository that already declares its members is not double-counted."""
    _write(
        repository,
        ".concordat",
        "language:\n  rust:\n    surfaces:\n"
        "      - path: Cargo.toml\n      - path: native/Cargo.toml\n",
    )
    _write(repository, "Cargo.toml", _workspace('["native"]'))
    _write(repository, "native/Cargo.toml", _member())

    declarations = build_dependency_family_envelope(repository)["declarations"]

    assert [d["manifest"] for d in declarations] == ["native/Cargo.toml"]


@pytest.mark.parametrize("entry", ['""', "1", "true", '"["', '"**/"'])
def test_a_malformed_member_entry_is_ignored_not_fatal(
    repository: pathlib.Path, entry: str
) -> None:
    """An odd `members` entry neither raises nor widens what is read."""
    _write(repository, "Cargo.toml", _workspace(f'["native", {entry}]'))
    _write(repository, "native/Cargo.toml", _member())

    envelope = build_dependency_family_envelope(repository)

    assert "native/Cargo.toml" in {d["manifest"] for d in envelope["declarations"]}
