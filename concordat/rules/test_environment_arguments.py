"""Read the argument list of a lint attribute, for `rust-test-environment`.

An attribute such as `expect(clippy::disallowed_methods, reason = "...")` has a
token tree for its arguments. This module splits it on its top-level commas,
reads each lint path, and decides whether a `reason` is a string literal with
visible content. Ordinary, raw and multi-line string literals all count.
"""

from __future__ import annotations

import itertools
import typing as typ

from .prover_rust_use import node_text

if typ.TYPE_CHECKING:
    import tree_sitter

__all__ = ["Items", "has_reason", "lint_paths", "top_level_items"]

LITERALS: typ.Final = frozenset({"string_literal", "raw_string_literal"})
PATH_TOKENS: typ.Final = frozenset({"identifier", "::"})
PARENTHESES: typ.Final = frozenset({"(", ")"})

type Items = list[list[tree_sitter.Node]]


def top_level_items(tree: tree_sitter.Node) -> Items:
    """Split an argument tree's own tokens on commas, ignoring nested trees.

    Returns
    -------
    Items
        The tokens of each comma-separated argument.
    """
    tokens = [child for child in tree.children if child.type not in PARENTHESES]
    groups = itertools.groupby(tokens, key=lambda token: token.type == ",")
    return [list(group) for is_comma, group in groups if not is_comma]


def _lint_path(item: list[tree_sitter.Node]) -> str | None:
    """Return `clippy::disallowed_methods`-style text for a lint item, if one."""
    if any(token.type not in PATH_TOKENS for token in item):
        return None
    return "".join(node_text(token) for token in item) or None


def lint_paths(items: Items) -> list[str]:
    """Return the lint paths among an attribute's arguments.

    Returns
    -------
    list[str]
        `clippy::all` and the like; `reason = "..."` and other items are not
        lint paths.
    """
    paths = (_lint_path(item) for item in items)
    return [path for path in paths if path is not None]


def _literal_text(literal: tree_sitter.Node) -> str:
    """Return a string literal's content, or an empty string for any other node."""
    if literal.type not in LITERALS:
        return ""
    return "".join(
        node_text(part) for part in literal.children if part.type == "string_content"
    )


def _reason_value(item: list[tree_sitter.Node]) -> tree_sitter.Node | None:
    """Return the value node of a `reason = value` item, or None."""
    if len(item) < 3:
        return None
    head, equals, value = item[:3]
    if node_text(head) != "reason":
        return None
    return value if equals.type == "=" else None


def has_reason(items: Items) -> bool:
    """Return whether a `reason` is a string literal with visible content."""
    values = (_reason_value(item) for item in items)
    texts = (_literal_text(value) for value in values if value is not None)
    return any(text.strip() for text in texts)
