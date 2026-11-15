"""Find the attributes that silence the process-environment lint in Rust files.

Clippy, after macro expansion, finds the calls to `std::env::set_var`,
`remove_var` and `set_current_dir`. This module only finds the ways its verdict
could be switched off (TE-003), so a file is read with tree-sitter and never
searched as text. Attributes come from two places:

- attribute items and inner attributes in the syntax tree; and
- attribute-shaped tokens inside macro bodies and invocations (`#[allow(..)]` in
  a `macro_rules!` transcriber), which tree-sitter leaves as token trees but
  which Clippy honours once the macro expands.

`test_environment_attributes` decides what each attribute means. A file that
cannot be read, or whose syntax error names the lint, one of its groups or
`warnings`, is reported as unreadable: an unread file is not evidence of
absence. A syntax error elsewhere is ignored, because syntax the parser
predates cannot hide a suppression of this lint.
"""

from __future__ import annotations

import dataclasses
import os
import typing as typ

import tree_sitter
import tree_sitter_rust

from .prover_rust_use import descendants, node_text
from .test_environment_attributes import (
    AttributeSite,
    Suppression,
    judge,
    site_scope,
)

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    import pathlib

__all__ = [
    "SKIPPED_DIRECTORIES",
    "SourceScan",
    "Suppression",
    "scan_suppressions",
]

_LANGUAGE: typ.Final = tree_sitter.Language(tree_sitter_rust.language())

SKIPPED_DIRECTORIES: typ.Final = frozenset({
    "target",
    "node_modules",
    ".git",
    ".venv",
    "fixtures",
    "fixture",
    "vendor",
})
ATTRIBUTE_ITEMS: typ.Final = frozenset({"attribute_item", "inner_attribute_item"})
# Words that make a syntax error relevant: the lint, the groups it is in, and
# the group of every warning.
RELEVANT_WORDS: typ.Final = (
    "disallowed_methods",
    "clippy::style",
    "clippy::all",
    "warnings",
)


@dataclasses.dataclass(slots=True)
class SourceScan:
    """The suppressions found, and the files that could not be judged."""

    suppressions: list[Suppression] = dataclasses.field(default_factory=list)
    unreadable: list[str] = dataclasses.field(default_factory=list)


def _item_site(node: tree_sitter.Node) -> list[AttributeSite]:
    """Return the attribute an `attribute_item` or `inner_attribute_item` holds."""
    attribute = next((c for c in node.children if c.type == "attribute"), None)
    if attribute is None:
        return []
    arguments = next((c for c in attribute.children if c.type == "token_tree"), None)
    if arguments is None or not attribute.children:
        return []
    return [
        AttributeSite(
            node_text(attribute.children[0]),
            arguments,
            node.start_point.row + 1,
            scope_ok=site_scope(node),
        )
    ]


def _macro_attribute(
    hash_token: tree_sitter.Node,
) -> list[AttributeSite]:
    """Return the attribute written as `#[name(..)]` or `#![name(..)]` tokens."""
    following = hash_token.next_sibling
    if following is not None and following.type == "!":
        following = following.next_sibling
    if following is None or following.type != "token_tree":
        return []
    named = [c for c in following.children if c.is_named]
    if not named or named[0].type != "identifier":
        return []
    arguments = next((c for c in named[1:] if c.type == "token_tree"), None)
    if arguments is None:
        return []
    return [
        AttributeSite(
            node_text(named[0]),
            arguments,
            hash_token.start_point.row + 1,
            scope_ok=True,
        )
    ]


def _token_tree_sites(tree: tree_sitter.Node) -> list[AttributeSite]:
    """Return the attribute-shaped tokens directly inside a token tree."""
    return [
        site
        for child in tree.children
        if child.type == "#"
        for site in _macro_attribute(child)
    ]


def _sites_of(node: tree_sitter.Node) -> list[AttributeSite]:
    """Return the attributes a syntax node is, or directly holds as tokens."""
    if node.type in ATTRIBUTE_ITEMS:
        return _item_site(node)
    if node.type == "token_tree":
        return _token_tree_sites(node)
    return []


def attribute_sites(root: tree_sitter.Node) -> cabc.Iterator[AttributeSite]:
    """Yield each attribute in a file, in the syntax tree and inside macros.

    Yields
    ------
    AttributeSite
        One entry per attribute, with its name, arguments and line.
    """
    for node in descendants(root):
        yield from _sites_of(node)


def _error_line(root: tree_sitter.Node) -> int | None:
    """Return the line of a syntax error naming the lint or a group, if any."""
    if not root.has_error:
        return None
    return next(
        (
            node.start_point.row + 1
            for node in descendants(root)
            if (node.type == "ERROR" or node.is_missing)
            and any(word in node_text(node) for word in RELEVANT_WORDS)
        ),
        None,
    )


def _file_facts(
    source: pathlib.Path,
    checkout: pathlib.Path,
    parser: tree_sitter.Parser,
    silencing: frozenset[str],
) -> tuple[list[Suppression], str | None]:
    """Return one file's suppressions, or the reason it could not be judged."""
    relative = source.relative_to(checkout).as_posix()
    try:
        data = source.read_bytes()
    except OSError as error:
        return [], f"{relative}: {error}"
    root = parser.parse(data).root_node
    line = _error_line(root)
    if line is not None:
        return [], (
            f"{relative}: syntax the parser could not read near line {line} "
            "names the disallowed_methods lint, so whether it is suppressed is "
            "unknown"
        )
    return [
        suppression
        for site in attribute_sites(root)
        for suppression in judge(site, relative, silencing)
    ], None


def _is_walked(base: pathlib.Path, name: str, members: frozenset[pathlib.Path]) -> bool:
    """Return whether the walk enters a directory.

    A nested directory with its own `Cargo.toml` that is not a member is
    another package (a fixture, an example workspace) that Clippy does not lint
    as part of this one. Links are not followed.

    Returns
    -------
    bool
        True when the directory belongs to the member being walked.
    """
    here = base / name
    if name in SKIPPED_DIRECTORIES or here.is_symlink():
        return False
    return here in members or not (here / "Cargo.toml").is_file()


def _walk(
    directory: pathlib.Path, members: frozenset[pathlib.Path], unreadable: list[str]
) -> list[pathlib.Path]:
    """Return the Rust files of a member crate, without entering other crates.

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
        found += [
            base / f
            for f in sorted(files)
            if f.endswith(".rs") and not (base / f).is_symlink()
        ]
    return found


def scan_suppressions(
    checkout: pathlib.Path,
    member_directories: typ.Sequence[pathlib.Path],
    silencing: typ.Iterable[str],
) -> SourceScan:
    """Return the suppressions in the Rust files of every member crate.

    Parameters
    ----------
    checkout:
        The checkout under audit; reported paths are relative to it.
    member_directories:
        The directory of each workspace member (absolute).
    silencing:
        The lint paths that silence the rule: the lint and its groups.

    Returns
    -------
    SourceScan
        Findings sorted by path and line, and the files that could not be read.
    """
    scan = SourceScan()
    parser = tree_sitter.Parser(_LANGUAGE)
    lints = frozenset(silencing)
    members = frozenset(member_directories)
    sources = sorted({
        source
        for directory in sorted(members)
        for source in _walk(directory, members, scan.unreadable)
    })
    for source in sources:
        found, problem = _file_facts(source, checkout, parser, lints)
        scan.suppressions += found
        scan.unreadable += [problem] if problem is not None else []
    scan.suppressions.sort(key=lambda s: (s.path, s.line, s.kind))
    return scan
