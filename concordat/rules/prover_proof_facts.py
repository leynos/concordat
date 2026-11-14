"""Find the proof code a repository holds, for `rust-prover-tooling` (PT-006).

Proof code that no job runs is a comment, so the rule has to know whether a
repository has any before it can ask whether CI runs it. The facts come from a
parser, never a text search: each Rust file is read with tree-sitter, so a
`#[kani::proof]` in a comment, a string or a snapshot is not proof code, and a
`use kani::proof as p; #[p]` is.

Three kinds are reported, at most once per file:

- `kani-harness`: an attribute that resolves to `kani::proof` or
  `kani::proof_for_contract`, through the file's `use` declarations, or a
  `cfg_attr` that names one.
- `verus-block`: a `verus!` or `verus_!` macro with a token-tree body, a
  `use vstd::...` declaration, or a `#[verifier::...]` attribute.
- `proof-generator`: a `quote!` or `quote_spanned!` in a proc-macro crate whose
  tokens emit `#[kani::proof]` or `verus!`. The harnesses such a crate writes
  are compiled by whoever uses it, and no ordinary build of the crate runs
  them, so the generator is proof code too (theoremc is the one known case).
  Whether a consumer elsewhere runs them is out of scope: that needs the
  dependency graph.

Files under `tests`, `test`, `fixtures`, `examples`, `target` and similar
trees are skipped, the same trees PT-004 skips (`prover_rust_files`). A file
that cannot be read, or that has a syntax error whose text names a prover, is
returned as unreadable: an unread file is not evidence of absence. A syntax
error elsewhere in a file (syntax the parser predates) is not reported, because
it cannot hide a proof.
"""

from __future__ import annotations

import dataclasses
import typing as typ

import tree_sitter
import tree_sitter_rust

from .prover_rust_files import ManifestCache, rust_files
from .prover_rust_use import (
    KANI_PROOF_PATHS,
    Bindings,
    bindings_of,
    descendants,
    node_text,
    path_of,
)

if typ.TYPE_CHECKING:
    import pathlib

    from .prover_tooling_types import ProofCode

__all__ = ["ProofScan", "scan_proof_code"]

# `<module>::<attribute>`: the shape of a path through a Kani module alias.
KANI_MODULE_PATH: typ.Final = ("module", "attribute")
VERUS_MACROS: typ.Final = frozenset({"verus", "verus_"})
GENERATOR_MACROS: typ.Final = frozenset({"quote", "quote_spanned"})
PROVER_WORDS: typ.Final = ("kani", "verus", "vstd", "verifier")
KIND_KANI: typ.Final = "kani-harness"
KIND_VERUS: typ.Final = "verus-block"
KIND_GENERATOR: typ.Final = "proof-generator"
KANI_TOKENS: typ.Final = ("#", "[", "kani", "::", "proof")
VERUS_TOKENS: typ.Final = ("verus", "!")

_LANGUAGE: typ.Final = tree_sitter.Language(tree_sitter_rust.language())


@dataclasses.dataclass(slots=True)
class ProofScan:
    """The proof code found, and the files that could not be read."""

    proof_code: list[ProofCode] = dataclasses.field(default_factory=list)
    unreadable: list[str] = dataclasses.field(default_factory=list)


def _leaves(node: tree_sitter.Node) -> list[str]:
    """Return the source text of every leaf under *node*, in order."""
    if node.child_count == 0:
        return [node_text(node)]
    return [leaf for child in node.children for leaf in _leaves(child)]


def _contains(tokens: list[str], pattern: tuple[str, ...]) -> bool:
    """Return whether *pattern* occurs as consecutive items of *tokens*."""
    width = len(pattern)
    return any(
        tuple(tokens[start : start + width]) == pattern
        for start in range(len(tokens) - width + 1)
    )


def _head_path(node: tree_sitter.Node) -> tuple[str, ...]:
    """Return the path a node's first named child spells, or an empty path."""
    head = next((c for c in node.children if c.is_named), None)
    return path_of(head) if head is not None else ()


def _attribute_resolves_to_proof(
    attribute: tree_sitter.Node, bindings: Bindings
) -> bool:
    """Return whether an attribute names `kani::proof` or an alias of it."""
    path = _head_path(attribute)
    if len(path) == 1 and path[0] in bindings.proof_names:
        return True
    if len(path) == len(KANI_MODULE_PATH) and path[0] in bindings.kani_modules:
        return ("kani", path[1]) in KANI_PROOF_PATHS
    return path == ("cfg_attr",) and _contains(
        _leaves(attribute), ("kani", "::", "proof")
    )


def _attribute_kind(item: tree_sitter.Node, bindings: Bindings) -> str | None:
    """Return the proof kind an `attribute_item` is, or None."""
    attribute = next((c for c in item.children if c.type == "attribute"), None)
    if attribute is None:
        return None
    if _head_path(attribute)[:1] == ("verifier",):
        return KIND_VERUS
    return KIND_KANI if _attribute_resolves_to_proof(attribute, bindings) else None


def _is_generator(body: tree_sitter.Node) -> bool:
    """Return whether a `quote!` body emits a Kani harness or a `verus!` block."""
    tokens = _leaves(body)
    return _contains(tokens, KANI_TOKENS) or _contains(tokens, VERUS_TOKENS)


def _macro_kind(
    node: tree_sitter.Node, *, proc_macro: typ.Callable[[], bool]
) -> str | None:
    """Return the proof kind a `macro_invocation` is, or None."""
    name = (_head_path(node) or ("",))[-1]
    body = next((c for c in node.children if c.type == "token_tree"), None)
    if body is None:
        return None
    if name in VERUS_MACROS:
        return KIND_VERUS
    wanted = name in GENERATOR_MACROS and _is_generator(body)
    return KIND_GENERATOR if wanted and proc_macro() else None


def _names_prover(node: tree_sitter.Node) -> bool:
    """Return whether a syntax-error node's text names a prover."""
    lowered = node_text(node).lower()
    return any(word in lowered for word in PROVER_WORDS)


def _error_names_prover(root: tree_sitter.Node) -> int | None:
    """Return the line of a syntax error whose text names a prover, if any."""
    if not root.has_error:
        return None
    return next(
        (
            node.start_point.row + 1
            for node in descendants(root)
            if (node.type == "ERROR" or node.is_missing) and _names_prover(node)
        ),
        None,
    )


def _node_kind(
    node: tree_sitter.Node,
    bindings: Bindings,
    proc_macro: typ.Callable[[], bool],
) -> str | None:
    """Return the proof kind of one node, or None."""
    match node.type:
        case "attribute_item":
            return _attribute_kind(node, bindings)
        case "macro_invocation":
            return _macro_kind(node, proc_macro=proc_macro)
        case _:
            return None


def _file_proof_code(
    root: tree_sitter.Node, *, proc_macro: typ.Callable[[], bool]
) -> list[tuple[str, int]]:
    """Return the `(kind, line)` of the first proof of each kind in a file."""
    bindings = bindings_of(root)
    found = [(KIND_VERUS, 1)] if bindings.uses_vstd else []
    found += [
        (kind, node.start_point.row + 1)
        for node in descendants(root)
        if (kind := _node_kind(node, bindings, proc_macro)) is not None
    ]
    kinds = {kind for kind, _ in found}
    return sorted((k, min(line for kind, line in found if kind == k)) for k in kinds)


def _file_facts(
    source: pathlib.Path,
    checkout: pathlib.Path,
    parser: tree_sitter.Parser,
    manifests: ManifestCache,
) -> tuple[list[ProofCode], str | None]:
    """Return one file's proof code, or the reason it could not be judged."""
    relative = source.relative_to(checkout).as_posix()
    try:
        data = source.read_bytes()
    except OSError as error:
        return [], f"{relative}: {error}"
    root = parser.parse(data).root_node
    line = _error_names_prover(root)
    if line is not None:
        return [], (
            f"{relative}: syntax the parser could not read near line {line} "
            "names a prover, so whether it is proof code is unknown"
        )
    found = _file_proof_code(root, proc_macro=lambda: manifests.is_proc_macro(source))
    return [{"path": relative, "kind": k, "line": line} for k, line in found], None


def scan_proof_code(checkout: pathlib.Path) -> ProofScan:
    """Return the proof code in a checkout's Rust sources.

    Returns
    -------
    ProofScan
        One entry per file and kind, sorted by path, and the files that could
        not be read or whose syntax error names a prover.
    """
    scan = ProofScan()
    parser = tree_sitter.Parser(_LANGUAGE)
    manifests = ManifestCache(checkout)
    for source in rust_files(checkout, scan.unreadable):
        proof_code, problem = _file_facts(source, checkout, parser, manifests)
        scan.proof_code += proof_code
        if problem is not None:
            scan.unreadable.append(problem)
    return scan
