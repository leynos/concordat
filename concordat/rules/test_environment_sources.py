"""Find the attributes that silence the process-environment lint in Rust files.

`rust-test-environment` (TE-003) holds a repository to one way of suppressing
`clippy::disallowed_methods` on a call to `std::env::set_var`, `remove_var` or
`set_current_dir`: `#[expect(clippy::disallowed_methods, reason = "...")]` on
the function, `let` or statement that makes the call. Clippy, after macro
expansion, is what finds the calls; this module only finds the ways its verdict
could be switched off, so a file is read with tree-sitter and never searched as
text. Three things are reported, each with its line:

- `allow`: an `allow` (also inside `cfg_attr`) that names the lint or a group
  that contains it, at any level.
- `expect-no-reason`: an `expect` of the lint with no `reason`, or a blank one.
- `expect-scope`: an `expect` that is a crate or module inner attribute, or an
  outer attribute on an item that can hold many sites (a `mod`, `trait`, a
  struct, and the like), because one attribute there silences every site added
  later. A function, `let`, statement, `const`, `static` and an `impl` block
  (the one named adapter behind an injection seam) are site scopes.

A file that cannot be read, or whose syntax error names the lint or one of its
groups, is reported as unreadable: an unread file is not evidence of absence.
A syntax error elsewhere is ignored, because syntax the parser predates cannot
hide a suppression of this lint.
"""

from __future__ import annotations

import dataclasses
import itertools
import os
import typing as typ

import tree_sitter
import tree_sitter_rust

from .prover_rust_use import descendants, node_text, path_of

if typ.TYPE_CHECKING:
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
LEVELS: typ.Final = frozenset({"allow", "expect"})
WRAPPING_ATTRIBUTES: typ.Final = frozenset({"cfg_attr"})
# An outer `expect` is a site-level suppression only on one of these. Anything
# that can hold several statements or items (`mod`, `trait`) is wider than one
# call, so it is an `expect-scope` finding.
SITE_ITEMS: typ.Final = frozenset({
    "function_item",
    "let_declaration",
    "expression_statement",
    "const_item",
    "static_item",
    # A trait impl that is the process-backed adapter behind an injection seam
    # (netsuke's `SystemEnv`): one named adapter, not a module of sites.
    "impl_item",
})
TRIVIA: typ.Final = frozenset({"attribute_item", "line_comment", "block_comment"})
# Words that make a syntax error relevant: the lint, and the groups it is in.
RELEVANT_WORDS: typ.Final = ("disallowed_methods", "clippy::style", "clippy::all")

KIND_ALLOW: typ.Final = "allow"
KIND_NO_REASON: typ.Final = "expect-no-reason"
KIND_SCOPE: typ.Final = "expect-scope"


@dataclasses.dataclass(slots=True, frozen=True)
class Suppression:
    """One attribute that silences the lint other than a site-level `expect`."""

    path: str
    line: int
    kind: str
    lint: str


@dataclasses.dataclass(slots=True)
class SourceScan:
    """The suppressions found, and the files that could not be judged."""

    suppressions: list[Suppression] = dataclasses.field(default_factory=list)
    unreadable: list[str] = dataclasses.field(default_factory=list)


def _clauses(tree: tree_sitter.Node) -> list[tuple[str, tree_sitter.Node]]:
    """Return each `(level, argument tree)` an attribute argument tree holds.

    `allow(a)` inside `cfg_attr(test, allow(a))` is found by looking, at every
    nesting depth, for a level name directly followed by a token tree.

    Returns
    -------
    list[tuple[str, tree_sitter.Node]]
        The level name and the token tree that follows it, for each clause.
    """
    found: list[tuple[str, tree_sitter.Node]] = []
    for node in descendants(tree):
        for here, following in itertools.pairwise(node.children):
            if (
                here.type == "identifier"
                and following.type == "token_tree"
                and node_text(here) in LEVELS
            ):
                found.append((node_text(here), following))
    return found


def _top_level_items(tree: tree_sitter.Node) -> list[list[tree_sitter.Node]]:
    """Split an argument tree's own tokens on commas, ignoring nested trees."""
    items: list[list[tree_sitter.Node]] = [[]]
    for child in tree.children:
        if child.type in {"(", ")"}:
            continue
        if child.type == ",":
            items.append([])
        else:
            items[-1].append(child)
    return [item for item in items if item]


def _lint_path(item: list[tree_sitter.Node]) -> str | None:
    """Return `clippy::disallowed_methods`-style text for a lint item, if one."""
    names = [node for node in item if node.type in {"identifier", "::"}]
    if len(names) != len(item) or not names:
        return None
    return "".join(node_text(node) for node in names)


def _reason_of(items: list[list[tree_sitter.Node]]) -> str | None:
    """Return the text of `reason = "..."`, or None when absent or not a string."""
    for item in items:
        head, *rest = item
        if (
            node_text(head) == "reason"
            and rest[:1]
            and rest[0].type == "="
            and rest[1:]
        ):
            literal = rest[1]
            if literal.type != "string_literal":
                return None
            return "".join(
                node_text(part)
                for part in literal.children
                if part.type == "string_content"
            )
    return None


def _site_scope(attribute: tree_sitter.Node) -> bool:
    """Return whether an attribute is outer and on an item that is one site.

    The target is the next sibling that is not another attribute or a comment.

    Returns
    -------
    bool
        True when the attribute is an outer one on a site-level item.
    """
    if attribute.type != "attribute_item":
        return False
    target = attribute.next_named_sibling
    while target is not None and target.type in TRIVIA:
        target = target.next_named_sibling
    return target is not None and target.type in SITE_ITEMS


def _attribute_suppressions(
    attribute: tree_sitter.Node, relative: str, silencing: frozenset[str]
) -> list[Suppression]:
    """Return what one attribute item suppresses that the rule does not allow."""
    inner = next((c for c in attribute.children if c.type == "attribute"), None)
    arguments = (
        None
        if inner is None
        else next((c for c in inner.children if c.type == "token_tree"), None)
    )
    if inner is None or arguments is None:
        return []
    head = path_of(inner.children[0]) if inner.children else ()
    levelled = (
        [(node_text(inner.children[0]), arguments)]
        if head and head[0] in LEVELS
        else []
    )
    if head and head[0] in WRAPPING_ATTRIBUTES:
        levelled = _clauses(arguments)
    line = attribute.start_point.row + 1
    found: list[Suppression] = []
    for level, tree in levelled:
        items = _top_level_items(tree)
        lints = [lint for item in items if (lint := _lint_path(item)) is not None]
        for lint in (lint for lint in lints if lint in silencing):
            found += _judge(level, lint, items, (attribute, relative, line))
    return found


def _judge(
    level: str,
    lint: str,
    items: list[list[tree_sitter.Node]],
    where: tuple[tree_sitter.Node, str, int],
) -> list[Suppression]:
    """Return the findings for one `level(lint, ...)` clause."""
    attribute, relative, line = where
    if level == "allow":
        return [Suppression(relative, line, KIND_ALLOW, lint)]
    if lint != "clippy::disallowed_methods":
        # `expect(clippy::style)` also covers the lint, and a group expectation
        # is satisfied by any lint in the group, so it proves nothing here.
        return [Suppression(relative, line, KIND_SCOPE, lint)]
    reason = _reason_of(items)
    found: list[Suppression] = []
    if reason is None or not reason.strip():
        found.append(Suppression(relative, line, KIND_NO_REASON, lint))
    if not _site_scope(attribute):
        found.append(Suppression(relative, line, KIND_SCOPE, lint))
    return found


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
            "names the disallowed_methods lint, so whether it is suppressed is unknown"
        )
    found = [
        suppression
        for node in descendants(root)
        if node.type in {"attribute_item", "inner_attribute_item"}
        for suppression in _attribute_suppressions(node, relative, silencing)
    ]
    return found, None


def _walk(
    directory: pathlib.Path, members: frozenset[pathlib.Path], unreadable: list[str]
) -> list[pathlib.Path]:
    """Return the Rust files of a member crate, without entering other crates.

    A nested directory with its own `Cargo.toml` that is not a member is
    another package (a fixture, an example workspace) that Clippy does not lint
    as part of this one, so the walk leaves it alone. Links are not followed.

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
        names[:] = sorted(
            n
            for n in names
            if n not in SKIPPED_DIRECTORIES
            and not (base / n).is_symlink()
            and not ((base / n / "Cargo.toml").is_file() and (base / n) not in members)
        )
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
    seen: set[pathlib.Path] = set()
    for directory in sorted(members):
        for source in _walk(directory, members, scan.unreadable):
            if source in seen:
                continue
            seen.add(source)
            found, problem = _file_facts(source, checkout, parser, lints)
            scan.suppressions += found
            if problem is not None:
                scan.unreadable.append(problem)
    scan.suppressions.sort(key=lambda s: (s.path, s.line, s.kind))
    return scan
