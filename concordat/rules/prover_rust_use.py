"""Read what a Rust file's `use` declarations bind, for proof-code detection.

`#[proof]` is a Kani harness only when `proof` was imported from `kani`, and
`#[k::proof]` only when `k` is `kani`. This module resolves that from the parsed
use trees of one file: which names stand for `kani::proof` or
`kani::proof_for_contract`, which names stand for the `kani` module, and whether
the file imports `vstd`. `prover_proof_facts` reads attributes against it.
"""

from __future__ import annotations

import dataclasses
import typing as typ

if typ.TYPE_CHECKING:
    import collections.abc as cabc

    import tree_sitter

__all__ = [
    "KANI_PROOF_NAMES",
    "KANI_PROOF_PATHS",
    "Bindings",
    "bindings_of",
    "descendants",
    "node_text",
    "path_of",
]

KANI_PROOF_PATHS: typ.Final = frozenset({
    ("kani", "proof"),
    ("kani", "proof_for_contract"),
})
KANI_PROOF_NAMES: typ.Final = frozenset(path[1] for path in KANI_PROOF_PATHS)
KANI_PATH: typ.Final = ("kani",)
WILDCARD: typ.Final = "*"
PATH_PARTS: typ.Final = frozenset({"identifier", "crate", "self", "super"})

type UsePath = tuple[tuple[str, ...], str | None]


def node_text(node: tree_sitter.Node) -> str:
    """Return a node's source text."""
    return (node.text or b"").decode("utf-8", errors="replace")


def path_of(node: tree_sitter.Node) -> tuple[str, ...]:
    """Return the identifiers of a (scoped) path node, in order.

    Returns
    -------
    tuple[str, ...]
        The identifiers: `kani::proof` is `("kani", "proof")` and a bare `p` is
        `("p",)`. A node that is not a path gives an empty tuple.
    """
    if node.type == "scoped_identifier":
        return tuple(part for child in node.children for part in path_of(child))
    return (node_text(node),) if node.type in PATH_PARTS else ()


def _named(node: tree_sitter.Node) -> list[tree_sitter.Node]:
    """Return a node's named children."""
    return [child for child in node.children if child.is_named]


def _plain(node: tree_sitter.Node, prefix: tuple[str, ...]) -> list[UsePath]:
    """Return the one path a plain `use a::b` names, bound to its last name."""
    path = prefix + path_of(node)
    return [(path, path[-1] if path else None)]


def _renamed(node: tree_sitter.Node, prefix: tuple[str, ...]) -> list[UsePath]:
    """Return the one path a `use a::b as c` names, bound to the new name."""
    inner, alias = node.children[0], node.children[-1]
    return [(prefix + path_of(inner), node_text(alias))]


def _wildcard(node: tree_sitter.Node, prefix: tuple[str, ...]) -> list[UsePath]:
    """Return the module a `use a::*` imports everything from."""
    scope = next(iter(_named(node)), None)
    return [(prefix + (path_of(scope) if scope is not None else ()), WILDCARD)]


def _grouped(node: tree_sitter.Node, prefix: tuple[str, ...]) -> list[UsePath]:
    """Return the paths of `use a::{b, c}`, each under the shared prefix."""
    named = _named(node)
    return [
        found
        for child in named[1:]
        for found in use_paths(child, prefix + path_of(named[0]))
    ]


def _listed(node: tree_sitter.Node, prefix: tuple[str, ...]) -> list[UsePath]:
    """Return the paths of a bare `{b, c}` list."""
    return [found for child in _named(node) for found in use_paths(child, prefix)]


USE_READERS: typ.Final[
    dict[str, cabc.Callable[[tree_sitter.Node, tuple[str, ...]], list[UsePath]]]
] = {
    "identifier": _plain,
    "scoped_identifier": _plain,
    "use_as_clause": _renamed,
    "use_wildcard": _wildcard,
    "scoped_use_list": _grouped,
    "use_list": _listed,
}


def use_paths(node: tree_sitter.Node, prefix: tuple[str, ...] = ()) -> list[UsePath]:
    """Return each `(path, bound name)` a use tree introduces.

    A wildcard is reported with `*` as its bound name, and a rename with its new
    name. `use kani::{any, proof}` yields two entries.

    Returns
    -------
    list[UsePath]
        Each path with the name it binds, or None when it binds none.
    """
    reader = USE_READERS.get(node.type)
    return reader(node, prefix) if reader is not None else []


@dataclasses.dataclass(slots=True)
class Bindings:
    """Names a file's `use` declarations bind that matter to proof detection."""

    proof_names: set[str] = dataclasses.field(default_factory=set)
    kani_modules: set[str] = dataclasses.field(default_factory=lambda: {"kani"})
    uses_vstd: bool = False

    def add(self, path: tuple[str, ...], bound: str | None) -> None:
        """Record what one use path binds."""
        if not path:
            return
        self.uses_vstd = self.uses_vstd or path[0] == "vstd"
        if bound is not None:
            self._bind(path, bound)

    def _bind(self, path: tuple[str, ...], bound: str) -> None:
        """Record a path bound to a name: a harness attribute or the module."""
        if path in KANI_PROOF_PATHS:
            self.proof_names.add(bound)
        elif path == KANI_PATH:
            self._bind_module(bound)

    def _bind_module(self, bound: str) -> None:
        """Record `use kani::*` (the attributes) or `use kani as k` (an alias)."""
        if bound == WILDCARD:
            self.proof_names |= KANI_PROOF_NAMES
        else:
            self.kani_modules.add(bound)


def descendants(root: tree_sitter.Node) -> cabc.Iterator[tree_sitter.Node]:
    """Yield *root* and every node under it, without recursion.

    Yields
    ------
    tree_sitter.Node
        Each node once, parents before the children they hold.
    """
    stack = [root]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(reversed(node.children))


def _declared_paths(root: tree_sitter.Node) -> list[UsePath]:
    """Return every `(path, bound name)` the file's use declarations introduce."""
    return [
        found
        for node in descendants(root)
        if node.type == "use_declaration"
        for child in _named(node)
        for found in use_paths(child)
    ]


def bindings_of(root: tree_sitter.Node) -> Bindings:
    """Collect the proof-relevant bindings of every use declaration in a file.

    Returns
    -------
    Bindings
        What the file's `use` declarations bind.
    """
    found = Bindings()
    for path, bound in _declared_paths(root):
        found.add(path, bound)
    return found
