"""Recognise a function that builds a crate it wrote into a temporary directory.

A test that makes up a crate (writes a `Cargo.toml` and a source file into a
temporary directory, then builds it) is the shape the NC-012 harness check looks
for. The check must not take a mention for a write, so three things have to be
tied together by the names the function binds:

- a write call whose *destination* is a manifest: its first argument holds the
  `"Cargo.toml"` literal as code, or a name bound to a statement that holds it
  and does not read it. A manifest literal in the *content* of a write, or in a
  read, is not a destination;
- that destination derives from a temporary directory, through up to three
  `let` bindings;
- a build location (`current_dir(..)`, a manifest path) that derives from the
  *same* temporary directory. Each directory is its own root: a manifest
  written under one and a build run under another is two crates.

Comments and strings are masked first, so a name in either is not a use.
"""

from __future__ import annotations

import dataclasses
import re
import typing as typ

from .nested_cargo_text import split_top_level
from .rust_source import scan_balanced

type Span = tuple[int, int]
type Origins = frozenset[str]

_MANIFEST_NAME: typ.Final = re.compile(r'"Cargo\.toml"')
_WRITE_CALL: typ.Final = re.compile(r"\b(?:write|write_all|create|create_new)\s*\(")
_READ_CALL: typ.Final = re.compile(r"\b(?:read\w*|open|exists)\s*\(")
_TEMPORARY: typ.Final = re.compile(r"\b(?:tempdir|tempdir_in|TempDir|tempfile)\b")
_BUILD_LOCATION: typ.Final = re.compile(r"current_dir|manifest[-_]path")
_STATEMENT: typ.Final = re.compile(r"[^;{}]*;")
_LET_NAME: typ.Final = re.compile(r"\s*let\s+(?:mut\s+)?(?P<name>[A-Za-z_]\w*)")
_WORD: typ.Final = re.compile(r"[A-Za-z_]\w*")
_DERIVATION_DEPTH: typ.Final = 3


@dataclasses.dataclass(frozen=True, slots=True)
class _Function:
    """Hold one function's text, its statements and what its `let`s derive from."""

    text: str
    masked: str
    spans: list[Span]
    provenance: dict[str, Origins]

    def origins(self, words: set[str]) -> Origins:
        """Return the temporary directories any of *words* derives from."""
        found: Origins = frozenset()
        for word in words:
            found |= self.provenance.get(word, frozenset())
        return found


def _words(masked: str) -> set[str]:
    """Return the identifiers in *masked*."""
    return set(_WORD.findall(masked))


def _holds_manifest(text: str, masked: str, span: Span) -> bool:
    """Report whether a span holds a `"Cargo.toml"` literal that is code."""
    return any(
        masked[found.start()] == '"'
        for found in _MANIFEST_NAME.finditer(text, span[0], span[1])
    )


def _bindings(masked: str, spans: list[Span]) -> dict[str, set[str]]:
    """Return, for each `let` name, the identifiers its statement uses."""
    uses: dict[str, set[str]] = {}
    for start, end in spans:
        bound = _LET_NAME.match(masked, start)
        if bound is not None:
            name = bound.group("name")
            uses[name] = _words(masked[start:end]) - {name}
    return uses


def _roots(masked: str, spans: list[Span]) -> dict[str, Origins]:
    """Return the names bound to a statement that creates a temporary directory.

    Returns
    -------
    dict[str, Origins]
        Each such name mapped to itself, as the root it stands for.
    """
    found: dict[str, Origins] = {}
    for start, end in spans:
        bound = _LET_NAME.match(masked, start)
        if bound is not None and _TEMPORARY.search(masked, start, end):
            found[bound.group("name")] = frozenset({bound.group("name")})
    return found


def _inherit(found: dict[str, Origins], uses: dict[str, set[str]]) -> None:
    """Give each name the roots of every derived name its statement uses."""
    for name, used in uses.items():
        inherited: Origins = frozenset()
        for word in used:
            inherited |= found.get(word, frozenset())
        if inherited:
            found[name] = found.get(name, frozenset()) | inherited


def _provenance(masked: str, spans: list[Span]) -> dict[str, Origins]:
    """Return, for each `let` name, the temporary directories it derives from.

    A name bound to a statement that creates a temporary directory is a root of
    its own. A name bound to a statement that uses a derived name inherits that
    name's roots, up to three bindings deep.

    Returns
    -------
    dict[str, Origins]
        The roots of each derived name; names with none are absent.
    """
    uses = _bindings(masked, spans)
    found = _roots(masked, spans)
    for _ in range(_DERIVATION_DEPTH):
        _inherit(found, uses)
    return found


def _is_manifest_binding(text: str, masked: str, span: Span) -> str | None:
    """Return the name a statement binds to a manifest path, if it does.

    Returns
    -------
    str | None
        The bound name when the statement is a `let` that holds a `"Cargo.toml"`
        literal as code and reads nothing; otherwise None.
    """
    bound = _LET_NAME.match(masked, span[0])
    if bound is None or _READ_CALL.search(masked, span[0], span[1]):
        return None
    return bound.group("name") if _holds_manifest(text, masked, span) else None


def _manifest_names(text: str, masked: str, spans: list[Span]) -> set[str]:
    """Return the names bound to a manifest path by a statement that reads nothing."""
    names = {_is_manifest_binding(text, masked, span) for span in spans}
    return {name for name in names if name is not None}


def _first_argument(text: str, masked: str, call: re.Match[str]) -> tuple[str, str]:
    """Return the first argument of a call, as written and as masked.

    Returns
    -------
    tuple[str, str]
        The argument's original and masked text; empty when it cannot be read.
    """
    close = scan_balanced(masked, call.end() - 1)
    if close is None:
        return "", ""
    original = split_top_level(text[call.end() : close - 1])
    plain = split_top_level(masked[call.end() : close - 1])
    return (original[0], plain[0]) if original and plain else ("", "")


def _is_manifest_path(
    original: str, plain: str, words: set[str], manifests: set[str]
) -> bool:
    """Report whether a write's first argument is a manifest path."""
    is_literal = _MANIFEST_NAME.search(original) is not None and '"' in plain
    return is_literal or bool(words & manifests)


def _destination_origins(function: _Function) -> list[Origins]:
    """Return the roots of each write whose destination is a manifest path.

    Returns
    -------
    list[Origins]
        One set per write whose first argument is a manifest path literal, or a
        name bound to one, and that derives from a temporary directory.
    """
    manifests = _manifest_names(function.text, function.masked, function.spans)
    found: list[Origins] = []
    for start, end in function.spans:
        for call in _WRITE_CALL.finditer(function.masked, start, end):
            original, plain = _first_argument(function.text, function.masked, call)
            words = _words(plain)
            if _is_manifest_path(original, plain, words, manifests):
                found.append(function.origins(words))
    return found


def _build_origins(function: _Function) -> list[Origins]:
    """Return the roots of each statement that points a build somewhere."""
    return [
        function.origins(_words(function.masked[start:end]))
        for start, end in function.spans
        if _BUILD_LOCATION.search(function.text, start, end)
    ]


def is_scratch_crate(text: str, masked: str) -> bool:
    """Report whether a function writes a manifest to a temporary directory and builds.

    Parameters
    ----------
    text:
        The function's source as written.
    masked:
        The same text with comments and strings blanked, so positions agree.

    Returns
    -------
    bool
        True when a manifest is written to a path derived from a temporary
        directory and a build location derives from that same directory.
    """
    spans = [(found.start(), found.end()) for found in _STATEMENT.finditer(masked)]
    function = _Function(text, masked, spans, _provenance(masked, spans))
    builds = _build_origins(function)
    return any(
        destination & build
        for destination in _destination_origins(function)
        for build in builds
    )
