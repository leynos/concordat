"""Decide whether a checkout holds any Rust source at all.

A `Cargo.toml` with no `.rs` file beside or below it (a packaging manifest, a
template, a Python project that happens to carry one) has no Rust build for the
Rust build rules to judge, so they read it as not applicable.
"""

from __future__ import annotations

import typing as typ

from .dependency_family_discovery import walk_files

if typ.TYPE_CHECKING:
    import pathlib


def has_rust_sources(checkout: pathlib.Path) -> bool:
    """Return whether any `.rs` file lies below *checkout*.

    Hidden directories and vendored or generated trees are not entered, as for
    every other walk the audit makes. A directory the walk cannot list may hold
    the only Rust files, so an incomplete scan answers True: an unreadable tree
    never turns a repository with sources into one without.

    Returns
    -------
    bool
        True when at least one Rust source file is found.
    """
    unreadable: list[str] = []
    files = walk_files(checkout, unreadable)
    return bool(unreadable) or any(path.suffix == ".rs" for path in files)
