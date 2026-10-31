"""A crate that resolves from a local path is not a dependency to judge.

Dependabot never offers a version update for a dependency that resolves from a
`path`, so it cannot drift from its family and the dependency-family collector
leaves it out. A path dependency that also names a registry `version` is a
published crate (a workspace crate other members depend on), so it stays in.
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


_WORKSPACE: typ.Final = "\n[workspace]\nmembers = []\n\n[workspace.dependencies]\n"


def _write(root: pathlib.Path, relative: str, text: str) -> None:
    """Write *text* at *relative* under *root*."""
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _packages(root: pathlib.Path) -> set[str]:
    """Return the packages the collector declares for *root*."""
    envelope = build_dependency_family_envelope(root)
    return {declaration["package"] for declaration in envelope["declarations"]}


def _checkout(root: pathlib.Path, dependencies: str, extra: str = "") -> None:
    """Write a one-crate checkout with the given `[dependencies]` text."""
    _write(root, ".github/dependabot.yml", _CONFIG)
    _write(
        root,
        "Cargo.toml",
        '[package]\nname = "demo"\nversion = "0.1.0"\n\n'
        f"[dependencies]\n{dependencies}\n{extra}",
    )


@pytest.fixture(name="repository")
def _repository(tmp_path: pathlib.Path) -> pathlib.Path:
    """Return an empty checkout directory."""
    return tmp_path


def test_a_path_only_dependency_is_not_collected(repository: pathlib.Path) -> None:
    """A local crate with no registry version cannot drift, so it is left out."""
    _checkout(repository, 'local = { path = "../local" }\nserde = "1"')

    assert _packages(repository) == {"serde"}


def test_a_path_dependency_with_a_version_is_still_collected(
    repository: pathlib.Path,
) -> None:
    """A published workspace crate names a version, so it stays in the audit."""
    _checkout(repository, 'local = { path = "../local", version = "0.10.0" }')

    assert _packages(repository) == {"local"}


def test_a_workspace_inherited_path_only_dependency_is_not_collected(
    repository: pathlib.Path,
) -> None:
    """Inheriting a path-only entry from `[workspace.dependencies]` changes nothing."""
    _checkout(
        repository,
        'local.workspace = true\nserde = "1"',
        _WORKSPACE + 'local = { path = "local" }\n',
    )

    assert _packages(repository) == {"serde"}


def test_a_workspace_inherited_versioned_path_dependency_is_collected(
    repository: pathlib.Path,
) -> None:
    """An inherited entry with a registry version is still judged."""
    _checkout(
        repository,
        "local.workspace = true",
        _WORKSPACE + 'local = { path = "local", version = "1" }\n',
    )

    assert _packages(repository) == {"local"}
