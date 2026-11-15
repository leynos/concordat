"""Find the attributes in a parsed Rust file, for `rust-test-environment`.

Clippy honours an attribute once macros have expanded, so attributes come from
two places:

- attribute items and inner attributes in the syntax tree; and
- attribute-shaped tokens inside macro bodies and invocations (`#[allow(..)]` in
  a `macro_rules!` transcriber), which tree-sitter leaves as token trees.

`test_environment_attributes` decides what each attribute means.
"""

from __future__ import annotations

import typing as typ

from .prover_rust_use import descendants, node_text
from .test_environment_arguments import significant
from .test_environment_attributes import AttributeSite, site_scope

if typ.TYPE_CHECKING:
    import collections.abc as cabc

    import tree_sitter

__all__ = ["attribute_sites"]

ATTRIBUTE_ITEMS: typ.Final = frozenset({"attribute_item", "inner_attribute_item"})


def _child(node: tree_sitter.Node, kind: str) -> tree_sitter.Node | None:
    """Return the first child of a syntax kind, if any."""
    return next((c for c in node.children if c.type == kind), None)


def _item_site(node: tree_sitter.Node) -> list[AttributeSite]:
    """Return the attribute an `attribute_item` or `inner_attribute_item` holds."""
    attribute = _child(node, "attribute")
    if attribute is None:
        return []
    arguments = _child(attribute, "token_tree")
    if arguments is None:
        return []
    return [
        AttributeSite(
            node_text(attribute.children[0]),
            arguments,
            node.start_point.row + 1,
            scope_ok=site_scope(node),
        )
    ]


def _after_hash(hash_token: tree_sitter.Node) -> tree_sitter.Node | None:
    """Return the token after `#`, skipping the `!` of an inner attribute."""
    following = hash_token.next_sibling
    if following is not None and following.type == "!":
        return following.next_sibling
    return following


def _site_from_tokens(named: list[tree_sitter.Node], line: int) -> list[AttributeSite]:
    """Return the attribute whose name and argument tree are the given tokens."""
    if not named or named[0].type != "identifier":
        return []
    arguments = next((c for c in named[1:] if c.type == "token_tree"), None)
    if arguments is None:
        return []
    return [AttributeSite(node_text(named[0]), arguments, line, scope_ok=True)]


def _macro_attribute(hash_token: tree_sitter.Node) -> list[AttributeSite]:
    """Return the attribute written as `#[name(..)]` or `#![name(..)]` tokens."""
    brackets = _after_hash(hash_token)
    if brackets is None or brackets.type != "token_tree":
        return []
    named = [c for c in significant(brackets.children) if c.is_named]
    return _site_from_tokens(named, hash_token.start_point.row + 1)


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
