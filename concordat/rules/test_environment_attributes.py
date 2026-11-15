"""Judge the attributes that silence the process-environment lint.

`rust-test-environment` (TE-003) holds a repository to one way of suppressing
`clippy::disallowed_methods` on a call to `std::env::set_var`, `remove_var` or
`set_current_dir`: `#[expect(clippy::disallowed_methods, reason = "...")]` on
the function, `let` or statement that makes the call. An attribute is judged
from its parsed argument tree, never from text. Three findings are reported:

- `allow`: an `allow` (also inside `cfg_attr`) that names the lint, a group that
  contains it (`clippy::style`, `clippy::all`), or `warnings`, at any level.
- `expect-no-reason`: an `expect` of the lint with no `reason`, or a blank one.
- `expect-scope`: an `expect` that is an inner attribute, an outer attribute on
  an item that can hold many sites (a `mod`, `trait`, a struct, and the like),
  or an `expect` of a group, because one attribute there silences every site
  added later.

`test_environment_sites` finds the attributes (including those written inside
a macro body, which Clippy honours after expansion); this module decides what
each one means.
"""

from __future__ import annotations

import dataclasses
import itertools
import typing as typ

from .prover_rust_use import descendants, node_text
from .test_environment_arguments import (
    Items,
    has_reason,
    lint_paths,
    significant,
    top_level_items,
)

if typ.TYPE_CHECKING:
    import tree_sitter

__all__ = ["AttributeSite", "Suppression", "judge", "site_scope"]

REQUIRED_LINT: typ.Final = "clippy::disallowed_methods"
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

KIND_ALLOW: typ.Final = "allow"
KIND_NO_REASON: typ.Final = "expect-no-reason"
KIND_SCOPE: typ.Final = "expect-scope"

type Clause = tuple[str, tree_sitter.Node]


@dataclasses.dataclass(slots=True, frozen=True)
class Suppression:
    """One attribute that silences the lint other than a site-level `expect`."""

    path: str
    line: int
    kind: str
    lint: str


@dataclasses.dataclass(slots=True, frozen=True)
class AttributeSite:
    """An attribute found in source: its name, argument tree and position.

    `scope_ok` says whether an `expect` here sits on one site. It is false for
    an inner attribute or one on a wide item, and true for attributes written
    inside a macro body, where the item they will land on is not known.
    """

    name: str
    arguments: tree_sitter.Node
    line: int
    scope_ok: bool


def site_scope(attribute: tree_sitter.Node) -> bool:
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


def _is_clause(here: tree_sitter.Node, following: tree_sitter.Node) -> bool:
    """Return whether two adjacent tokens are a level name and its arguments."""
    return (
        here.type == "identifier"
        and following.type == "token_tree"
        and node_text(here) in LEVELS
    )


def _clauses(tree: tree_sitter.Node) -> list[Clause]:
    """Return each `(level, argument tree)` an attribute argument tree holds.

    `allow(a)` inside `cfg_attr(test, allow(a))` is found by looking, at every
    nesting depth, for a level name directly followed by a token tree.

    Returns
    -------
    list[Clause]
        The level name and the token tree that follows it, for each clause.
    """
    return [
        (node_text(here), following)
        for node in descendants(tree)
        for here, following in itertools.pairwise(significant(node.children))
        if _is_clause(here, following)
    ]


def _levelled(site: AttributeSite) -> list[Clause]:
    """Return the `(level, arguments)` clauses an attribute carries."""
    if site.name in LEVELS:
        return [(site.name, site.arguments)]
    if site.name in WRAPPING_ATTRIBUTES:
        return _clauses(site.arguments)
    return []


def _findings(
    level: str, lint: str, items: Items, site: AttributeSite
) -> list[tuple[str, str]]:
    """Return the `(kind, lint)` findings for one `level(lint, ...)` clause."""
    if level == "allow":
        return [(KIND_ALLOW, lint)]
    if lint != REQUIRED_LINT:
        # `expect(clippy::style)` also covers the lint, and a group expectation
        # is satisfied by any lint in the group, so it proves nothing here.
        return [(KIND_SCOPE, lint)]
    return [
        *([(KIND_NO_REASON, lint)] if not has_reason(items) else []),
        *([(KIND_SCOPE, lint)] if not site.scope_ok else []),
    ]


def _clause_suppressions(
    clause: Clause, site: AttributeSite, relative: str, silencing: frozenset[str]
) -> list[Suppression]:
    """Return what one `level(...)` clause suppresses that the rule disallows."""
    level, tree = clause
    items = top_level_items(tree)
    lints = [lint for lint in lint_paths(items) if lint in silencing]
    return [
        Suppression(relative, site.line, kind, found)
        for lint in lints
        for kind, found in _findings(level, lint, items, site)
    ]


def judge(
    site: AttributeSite, relative: str, silencing: frozenset[str]
) -> list[Suppression]:
    """Return what one attribute suppresses that the rule does not allow.

    Returns
    -------
    list[Suppression]
        The findings for the attribute, in source order of its clauses.
    """
    return [
        suppression
        for clause in _levelled(site)
        for suppression in _clause_suppressions(clause, site, relative, silencing)
    ]
