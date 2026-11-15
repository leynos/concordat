"""Walk a workspace member's tree for Rust files, for `rust-test-environment`.

The walk skips build output and vendored code, never follows a symbolic link,
and records a directory it cannot list instead of stopping. A nested directory
with its own `Cargo.toml` that is not a member is another package (a fixture,
an example workspace) that Clippy does not lint as part of this one, so it is
not entered.
"""

from __future__ import annotations

import os
import typing as typ

if typ.TYPE_CHECKING:
    import pathlib

__all__ = ["SKIPPED_DIRECTORIES", "rust_files"]

SKIPPED_DIRECTORIES: typ.Final = frozenset({
    "target",
    "node_modules",
    ".git",
    ".venv",
    "fixtures",
    "fixture",
    "vendor",
})


def _is_walked(base: pathlib.Path, name: str, members: frozenset[pathlib.Path]) -> bool:
    """Return whether the walk enters a directory.

    Returns
    -------
    bool
        True when the directory belongs to the member being walked.
    """
    here = base / name
    if name in SKIPPED_DIRECTORIES or here.is_symlink():
        return False
    return here in members or not (here / "Cargo.toml").is_file()


def _rust_sources(base: pathlib.Path, files: list[str]) -> list[pathlib.Path]:
    """Return the Rust sources among a directory's files, links excluded."""
    return [
        base / name
        for name in sorted(files)
        if name.endswith(".rs") and not (base / name).is_symlink()
    ]


def rust_files(
    directory: pathlib.Path, members: frozenset[pathlib.Path], unreadable: list[str]
) -> list[pathlib.Path]:
    """Return the Rust files of a member crate, without entering other crates.

    A directory that cannot be listed is appended to *unreadable* and the walk
    carries on with the others.

    Returns
    -------
    list[pathlib.Path]
        The Rust files, in a stable order.
    """

    def refuse(error: OSError) -> None:
        """Record a directory the walk could not list."""
        unreadable.append(f"{error.filename}: {error.strerror}")

    found: list[pathlib.Path] = []
    for here, names, files in os.walk(directory, onerror=refuse):
        base = directory.joinpath(here)
        names[:] = sorted(n for n in names if _is_walked(base, n, members))
        found += _rust_sources(base, files)
    return found
