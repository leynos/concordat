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
trees are skipped, the same trees PT-004 skips. A file that cannot be read, or
that has a syntax error whose text names a prover, is returned as unreadable:
an unread file is not evidence of absence. A syntax error elsewhere in a file
(syntax the parser predates) is not reported, because it cannot hide a proof.
"""

from __future__ import annotations

import dataclasses
import os
import tomllib
import typing as typ

import tree_sitter
import tree_sitter_rust

from .prover_rust_use import KANI_PROOF_PATHS, Bindings, bindings_of, node_text, path_of

if typ.TYPE_CHECKING:
    import pathlib

    from .prover_tooling_types import ProofCode

__all__ = ["ProofScan", "scan_proof_code"]

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


def _attribute_resolves_to_proof(
    attribute: tree_sitter.Node, bindings: Bindings
) -> bool:
    """Return whether an attribute names `kani::proof` or an alias of it."""
    head = next((c for c in attribute.children if c.is_named), None)
    if head is None:
        return False
    path = path_of(head)
    if len(path) == 1 and path[0] in bindings.proof_names:
        return True
    if len(path) == len(KANI_MODULE_PATH) and path[0] in bindings.kani_modules:
        return ("kani", path[1]) in KANI_PROOF_PATHS
    if path == ("cfg_attr",):
        return _contains(_leaves(attribute), ("kani", "::", "proof"))
    return False


def _attribute_kind(item: tree_sitter.Node, bindings: Bindings) -> str | None:
    """Return the proof kind an `attribute_item` is, or None."""
    attribute = next((c for c in item.children if c.type == "attribute"), None)
    if attribute is None:
        return None
    head = next((c for c in attribute.children if c.is_named), None)
    if head is not None and path_of(head)[:1] == ("verifier",):
        return KIND_VERUS
    return KIND_KANI if _attribute_resolves_to_proof(attribute, bindings) else None


def _macro_name(node: tree_sitter.Node) -> str:
    """Return the last identifier of a macro invocation's name."""
    head = next((c for c in node.children if c.is_named), None)
    path = path_of(head) if head is not None else ()
    return path[-1] if path else ""


def _is_proc_macro_crate(
    source: pathlib.Path, checkout: pathlib.Path, cache: dict[pathlib.Path, bool]
) -> bool:
    """Return whether the nearest Cargo manifest above *source* is a proc-macro."""
    directory = source.parent
    while True:
        manifest = directory / "Cargo.toml"
        if manifest.is_file():
            if manifest not in cache:
                cache[manifest] = _manifest_is_proc_macro(manifest)
            return cache[manifest]
        if directory in {checkout, directory.parent}:
            return False
        directory = directory.parent


def _manifest_is_proc_macro(manifest: pathlib.Path) -> bool:
    """Return whether a manifest declares `[lib] proc-macro = true`."""
    try:
        document = tomllib.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return False
    library = document.get("lib")
    return isinstance(library, dict) and library.get("proc-macro") is True


def _macro_kind(node: tree_sitter.Node, *, proc_macro: bool) -> str | None:
    """Return the proof kind a `macro_invocation` is, or None."""
    name = _macro_name(node)
    body = next((c for c in node.children if c.type == "token_tree"), None)
    if body is None:
        return None
    if name in VERUS_MACROS:
        return KIND_VERUS
    if name in GENERATOR_MACROS and proc_macro:
        tokens = _leaves(body)
        if _contains(tokens, KANI_TOKENS) or _contains(tokens, VERUS_TOKENS):
            return KIND_GENERATOR
    return None


def _error_names_prover(root: tree_sitter.Node) -> int | None:
    """Return the line of a syntax error whose text names a prover, if any."""
    if not root.has_error:
        return None
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type == "ERROR" or node.is_missing:
            lowered = node_text(node).lower()
            if any(word in lowered for word in PROVER_WORDS):
                return node.start_point.row + 1
        if node.has_error:
            stack.extend(node.children)
    return None


def _file_proof_code(
    root: tree_sitter.Node, *, proc_macro: typ.Callable[[], bool]
) -> list[tuple[str, int]]:
    """Return the `(kind, line)` of the first proof of each kind in a file."""
    bindings = bindings_of(root)
    first: dict[str, int] = {}
    if bindings.uses_vstd:
        first[KIND_VERUS] = 1
    stack = [root]
    while stack:
        node = stack.pop()
        match node.type:
            case "attribute_item":
                kind = _attribute_kind(node, bindings)
            case "macro_invocation":
                kind = _macro_kind(node, proc_macro=proc_macro())
            case _:
                kind = None
        if kind is not None:
            line = node.start_point.row + 1
            first[kind] = min(first.get(kind, line), line)
        stack.extend(node.children)
    return sorted(first.items())


def _rust_files(checkout: pathlib.Path, scan: ProofScan) -> list[pathlib.Path]:
    """Return the Rust files outside skipped trees, recording unreadable directories."""

    def refuse(error: OSError) -> None:
        """Record a directory the walk could not list."""
        scan.unreadable.append(f"{error.filename}: {error.strerror}")

    found: list[pathlib.Path] = []
    for directory, names, files in os.walk(checkout, onerror=refuse):
        here = checkout.joinpath(directory)
        names[:] = sorted(
            n
            for n in names
            if n not in SKIPPED_DIRECTORIES and not (here / n).is_symlink()
        )
        found += [
            here / f
            for f in sorted(files)
            if f.endswith(".rs") and not (here / f).is_symlink()
        ]
    return found


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
    manifests: dict[pathlib.Path, bool] = {}
    for source in _rust_files(checkout, scan):
        relative = source.relative_to(checkout).as_posix()
        try:
            data = source.read_bytes()
        except OSError as error:
            scan.unreadable.append(f"{relative}: {error}")
            continue
        root = parser.parse(data).root_node
        line = _error_names_prover(root)
        if line is not None:
            scan.unreadable.append(
                f"{relative}: syntax the parser could not read near line {line} "
                "names a prover, so whether it is proof code is unknown"
            )
            continue
        for kind, found in _file_proof_code(
            root,
            proc_macro=lambda s=source: _is_proc_macro_crate(s, checkout, manifests),
        ):
            scan.proof_code.append({"path": relative, "kind": kind, "line": found})
    return scan
