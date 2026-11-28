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
  same temporary directory.

Comments and strings are masked first, so a name in either is not a use.
"""

from __future__ import annotations

import re
import typing as typ

from .nested_cargo_text import split_top_level
from .rust_source import scan_balanced

_MANIFEST_NAME: typ.Final = re.compile(r'"Cargo\.toml"')
_WRITE_CALL: typ.Final = re.compile(r"\b(?:write|write_all|create|create_new)\s*\(")
_READ_CALL: typ.Final = re.compile(r"\b(?:read\w*|open|exists)\s*\(")
_TEMPORARY: typ.Final = re.compile(r"\b(?:tempdir|tempdir_in|TempDir|tempfile)\b")
_BUILD_LOCATION: typ.Final = re.compile(r"current_dir|manifest[-_]path")
_STATEMENT: typ.Final = re.compile(r"[^;{}]*;")
_LET_NAME: typ.Final = re.compile(r"\s*let\s+(?:mut\s+)?(?P<name>[A-Za-z_]\w*)")
_WORD: typ.Final = re.compile(r"[A-Za-z_]\w*")
_DERIVATION_DEPTH: typ.Final = 3


def _words(masked: str) -> set[str]:
    """Return the identifiers in *masked*."""
    return set(_WORD.findall(masked))


def _holds_manifest(text: str, masked: str, span: tuple[int, int]) -> bool:
    """Report whether a span holds a `"Cargo.toml"` literal that is code."""
    return any(
        masked[found.start()] == '"'
        for found in _MANIFEST_NAME.finditer(text, span[0], span[1])
    )


def _bindings(masked: str, spans: list[tuple[int, int]]) -> dict[str, set[str]]:
    """Return, for each `let` name, the identifiers its statement uses."""
    found: dict[str, set[str]] = {}
    for start, end in spans:
        bound = _LET_NAME.match(masked, start)
        if bound is not None:
            found[bound.group("name")] = _words(masked[start:end]) - {
                bound.group("name")
            }
    return found


def _temporary_names(
    masked: str, spans: list[tuple[int, int]], bindings: dict[str, set[str]]
) -> set[str]:
    """Return the names derived from a temporary directory.

    Returns
    -------
    set[str]
        The names bound to a statement that holds a temporary directory, then
        those bound to a statement using one of them, up to three levels deep.
    """
    names = {
        bound.group("name")
        for start, end in spans
        if (bound := _LET_NAME.match(masked, start)) is not None
        and _TEMPORARY.search(masked, start, end)
    }
    for _ in range(_DERIVATION_DEPTH):
        names |= {name for name, used in bindings.items() if used & names}
    return names


def _manifest_names(text: str, masked: str, spans: list[tuple[int, int]]) -> set[str]:
    """Return the names bound to a manifest path by a statement that writes nothing."""
    return {
        bound.group("name")
        for start, end in spans
        if _holds_manifest(text, masked, (start, end))
        and not _READ_CALL.search(masked, start, end)
        and (bound := _LET_NAME.match(masked, start)) is not None
    }


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


def _destinations(
    text: str, masked: str, spans: list[tuple[int, int]]
) -> list[set[str]]:
    """Return the identifiers of each write call's destination that is a manifest.

    Returns
    -------
    list[set[str]]
        One set per write whose first argument is a manifest path literal, or a
        name bound to one.
    """
    manifests = _manifest_names(text, masked, spans)
    found: list[set[str]] = []
    for start, end in spans:
        for call in _WRITE_CALL.finditer(masked, start, end):
            original, plain = _first_argument(text, masked, call)
            words = _words(plain)
            is_literal = _MANIFEST_NAME.search(original) and '"' in plain
            if is_literal or words & manifests:
                found.append(words)
    return found


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
        directory and a build location derives from the same directory.
    """
    spans = [(found.start(), found.end()) for found in _STATEMENT.finditer(masked)]
    bindings = _bindings(masked, spans)
    temporary = _temporary_names(masked, spans, bindings)
    if not temporary:
        return False
    # A name's own words include it, so a destination that is itself a derived
    # name counts as derived.
    is_written = any(
        destination & temporary
        or any(bindings.get(w, set()) & temporary for w in destination)
        for destination in _destinations(text, masked, spans)
    )
    return is_written and any(
        _BUILD_LOCATION.search(text, start, end)
        and _words(masked[start:end]) & temporary
        for start, end in spans
    )
