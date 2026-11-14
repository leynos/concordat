"""Find the Rust files to read for proof code, and the crates they belong to.

The walk skips the trees PT-004 skips (tests, fixtures, examples, build output
and vendored code), never follows a symbolic link, and records a directory it
cannot list instead of stopping. A file's crate is the nearest `Cargo.toml`
above it, which tells `prover_proof_facts` whether a `quote!` is a proc-macro
crate's generator or ordinary library text.
"""

from __future__ import annotations

import os
import tomllib
import typing as typ

if typ.TYPE_CHECKING:
    import pathlib

__all__ = ["SKIPPED_DIRECTORIES", "ManifestCache", "rust_files"]

SKIPPED_DIRECTORIES: typ.Final = frozenset({
    "target",
    "node_modules",
    ".git",
    ".venv",
    "fixtures",
    "fixture",
    "tests",
    "test",
    "examples",
})


def _is_wanted_directory(here: pathlib.Path, name: str) -> bool:
    """Return whether the walk should enter *name* under *here*."""
    return name not in SKIPPED_DIRECTORIES and not (here / name).is_symlink()


def _is_wanted_file(here: pathlib.Path, name: str) -> bool:
    """Return whether *name* under *here* is a Rust source that is not a link."""
    return name.endswith(".rs") and not (here / name).is_symlink()


def rust_files(checkout: pathlib.Path, unreadable: list[str]) -> list[pathlib.Path]:
    """Return the Rust files outside skipped trees.

    A directory that cannot be listed is appended to *unreadable* and the walk
    carries on with the others.

    Returns
    -------
    list[pathlib.Path]
        The files, in a stable order.
    """

    def refuse(error: OSError) -> None:
        """Record a directory the walk could not list."""
        unreadable.append(f"{error.filename}: {error.strerror}")

    found: list[pathlib.Path] = []
    for directory, names, files in os.walk(checkout, onerror=refuse):
        here = checkout.joinpath(directory)
        names[:] = sorted(n for n in names if _is_wanted_directory(here, n))
        found += [here / f for f in sorted(files) if _is_wanted_file(here, f)]
    return found


def _declares_proc_macro(manifest: pathlib.Path) -> bool:
    """Return whether a manifest declares `[lib] proc-macro = true`."""
    try:
        document = tomllib.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return False
    library = document.get("lib")
    return isinstance(library, dict) and library.get("proc-macro") is True


class ManifestCache:
    """Answer whether a Rust file's crate is a proc-macro crate, once per manifest."""

    def __init__(self, checkout: pathlib.Path) -> None:
        """Remember the checkout the search for a manifest stops at."""
        self._checkout = checkout
        self._answers: dict[pathlib.Path, bool] = {}

    def _manifest_above(self, source: pathlib.Path) -> pathlib.Path | None:
        """Return the nearest `Cargo.toml` above *source*, within the checkout."""
        for directory in (source.parent, *source.parent.parents):
            if (directory / "Cargo.toml").is_file():
                return directory / "Cargo.toml"
            if directory == self._checkout:
                break
        return None

    def is_proc_macro(self, source: pathlib.Path) -> bool:
        """Return whether the crate holding *source* is a proc-macro crate.

        Returns
        -------
        bool
            True when the nearest manifest declares `[lib] proc-macro = true`.
        """
        manifest = self._manifest_above(source)
        if manifest is None:
            return False
        if manifest not in self._answers:
            self._answers[manifest] = _declares_proc_macro(manifest)
        return self._answers[manifest]
