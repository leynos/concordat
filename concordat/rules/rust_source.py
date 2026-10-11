"""Read Rust attributes without a Rust parser.

The documentation-presence rule needs two things from Rust source: the
`missing_docs` lint attributes at the top of a crate root, and every
`allow`/`expect` of `missing_docs` elsewhere in the library. Both are found by
scanning for attribute syntax, so text that only looks like an attribute (a
doc example, a string, a comment) has to be blanked first; :func:`mask_literals`
does that without moving a byte or a line.
"""

from __future__ import annotations

import re
import typing as typ

LINT_NAME: typ.Final = "missing_docs"
LINT_KINDS: typ.Final = frozenset({"deny", "forbid", "warn", "allow", "expect"})
_ATTRIBUTE_START: typ.Final = re.compile(r"#!?\[")
_COMMENT_EDGE: typ.Final = re.compile(r"/\*|\*/")
_RAW_STRING_OPEN: typ.Final = re.compile(r'b?r(#*)"')

type Masked = tuple[str, int]


class RootAttribute(typ.TypedDict):
    """One lint attribute that names `missing_docs`."""

    kind: str
    has_reason: bool
    condition: str | None


def _string_close(text: str, index: int) -> int:
    """Return the index of the quote closing the string whose body starts at *index*."""
    while index < len(text) and text[index] != '"':
        index += 2 if text[index] == "\\" else 1
    return min(index, len(text))


def _block_comment_end(text: str, index: int) -> int:
    """Return the index just past the (nestable) block comment at *index*."""
    depth = 0
    for edge in _COMMENT_EDGE.finditer(text, index):
        depth += 1 if edge.group() == "/*" else -1
        if depth == 0:
            return edge.end()
    return len(text)


def _line_end(text: str, index: int) -> int:
    """Return the index of the newline ending the line at *index*, or the end."""
    end = text.find("\n", index)
    return len(text) if end == -1 else end


def _trivia_end(text: str, index: int) -> int | None:
    """Return the end of the whitespace or comment at *index*, or ``None``."""
    if text[index].isspace():
        return index + 1
    if text.startswith("//", index):
        return _line_end(text, index)
    if text.startswith("/*", index):
        return _block_comment_end(text, index)
    return None


def skip_trivia(text: str, index: int) -> int:
    """Return the index of the next token, past whitespace and comments."""
    while index < len(text):
        end = _trivia_end(text, index)
        if end is None:
            break
        index = end
    return index


def scan_balanced(text: str, start: int) -> int | None:
    """Return the index just past the bracket that closes the one at *start*.

    Strings are skipped, so a `reason` containing a bracket does not end the
    attribute early.

    Returns
    -------
    int | None
        The index after the closing bracket, or ``None`` if it never closes.
    """
    depth, index = 0, start
    while index < len(text):
        char = text[index]
        if char == '"':
            index = _string_close(text, index + 1)
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    return None


def _split_top_level(text: str) -> list[str]:
    """Split on commas that sit outside brackets and strings."""
    parts: list[str] = []
    depth, start, index = 0, 0, 0
    while index < len(text):
        char = text[index]
        if char == '"':
            index = _string_close(text, index + 1)
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif char == "," and depth == 0:
            parts.append(text[start:index].strip())
            start = index + 1
        index += 1
    parts.append(text[start:].strip())
    return [part for part in parts if part]


def _lint_attributes(body: str, condition: str | None) -> typ.Iterator[RootAttribute]:
    """Yield the `missing_docs` attributes inside one attribute's text.

    `cfg_attr(condition, inner...)` is unwrapped, carrying its condition, so
    `#![cfg_attr(not(test), deny(missing_docs))]` is seen for what it is.

    Yields
    ------
    RootAttribute
        One entry per lint attribute that names `missing_docs`.
    """
    head, _, rest = body.partition("(")
    name = head.strip()
    arguments = rest.rsplit(")", 1)[0]
    if name == "cfg_attr":
        condition_and_attributes = _split_top_level(arguments)
        for inner in condition_and_attributes[1:]:
            yield from _lint_attributes(inner, condition_and_attributes[0])
        return
    items = _split_top_level(arguments)
    lints = [item for item in items if not item.startswith("reason")]
    if name in LINT_KINDS and LINT_NAME in lints:
        yield {
            "kind": name,
            "has_reason": any(item.startswith("reason") for item in items),
            "condition": condition,
        }


def leading_attributes(source: str) -> tuple[list[RootAttribute], int]:
    """Return the `missing_docs` inner attributes at the top of a crate root.

    Inner attributes must precede every item, so reading stops at the first
    token that is not one. Comments and inner doc comments are skipped.

    Returns
    -------
    tuple[list[RootAttribute], int]
        The attributes in source order, and the index where the run ends.
    """
    found: list[RootAttribute] = []
    index = skip_trivia(source, 0)
    while source.startswith("#![", index):
        end = scan_balanced(source, index + 2)
        if end is None:
            break
        found.extend(_lint_attributes(source[index + 3 : end - 1], None))
        index = skip_trivia(source, end)
    return found, index


def _blank(text: str) -> str:
    """Return *text* with everything but newlines turned into spaces."""
    return "".join(char if char == "\n" else " " for char in text)


def _mask_line_comment(source: str, index: int) -> Masked | None:
    """Blank the `//` comment at *index*."""
    if not source.startswith("//", index):
        return None
    end = _line_end(source, index)
    return _blank(source[index:end]), end


def _mask_block_comment(source: str, index: int) -> Masked | None:
    """Blank the `/* */` comment at *index*."""
    if not source.startswith("/*", index):
        return None
    end = _block_comment_end(source, index)
    return _blank(source[index:end]), end


def _mask_raw_string(source: str, index: int) -> Masked | None:
    """Blank the body of the raw string at *index*, keeping its delimiters."""
    opening = _RAW_STRING_OPEN.match(source, index)
    if opening is None:
        return None
    body_start = opening.end()
    close = source.find('"' + opening.group(1), body_start)
    end = len(source) if close == -1 else close
    return opening.group(0) + _blank(source[body_start:end]), end


def _mask_string(source: str, index: int) -> Masked | None:
    """Blank the body of the string at *index*, keeping its quotes."""
    if source[index] != '"':
        return None
    end = _string_close(source, index + 1)
    closing = '"' if end < len(source) else ""
    return '"' + _blank(source[index + 1 : end]) + closing, end + len(closing)


def _mask_char(source: str, index: int) -> Masked | None:
    """Blank the character literal at *index*; a lifetime is left alone."""
    if source[index] != "'":
        return None
    if source.startswith("\\", index + 1):
        close = source.find("'", index + 3)
        end = None if close == -1 else close + 1
    else:
        end = index + 3 if source.startswith("'", index + 2) else None
    if end is None:
        return None
    return "'" + _blank(source[index + 1 : end - 1]) + "'", end


_MASKS: typ.Final = (
    _mask_line_comment,
    _mask_block_comment,
    _mask_raw_string,
    _mask_string,
    _mask_char,
)


def mask_literals(source: str) -> str:
    """Return *source* with comments and the contents of literals blanked out.

    Comments become spaces and string, raw string and byte string contents
    become spaces while their quotes stay, so a `reason = "..."` is still seen
    to have a value but `"#[allow(missing_docs)]"` in a string or a block
    comment is not an attribute. Character literals are blanked; a lifetime
    (`'a`) has no closing quote and is left alone. Newlines are kept, so line
    numbers do not move.

    Returns
    -------
    str
        A string of the same length and line structure as *source*.
    """
    out: list[str] = []
    index = 0
    while index < len(source):
        masked = next(
            (found for mask in _MASKS if (found := mask(source, index))), None
        )
        text, end = masked or (source[index], index + 1)
        out.append(text)
        index = end
    return "".join(out)


def suppressions_in(
    source: str, *, skip_before: int = 0
) -> list[tuple[int, str, bool]]:
    """Return the `allow`/`expect` of `missing_docs` anywhere in a source file.

    These are the module-level and item-level suppressions that hide
    undocumented items from a crate whose root denies the lint. Attributes that
    start before *skip_before* (the crate root's own leading run, which
    :func:`leading_attributes` already reports) are left out. Comments and
    literals are masked first (:func:`mask_literals`), so an attribute named in
    a comment, a doc example or a string is not one.

    Returns
    -------
    list[tuple[int, str, bool]]
        Line number, kind (`allow` or `expect`) and whether a `reason` is given.
    """
    masked = mask_literals(source)
    found: list[tuple[int, str, bool]] = []
    for match in _ATTRIBUTE_START.finditer(masked):
        end = scan_balanced(masked, match.end() - 1)
        if match.start() < skip_before or end is None:
            continue
        line = masked.count("\n", 0, match.start()) + 1
        found.extend(
            (line, attribute["kind"], attribute["has_reason"])
            for attribute in _lint_attributes(masked[match.end() : end - 1], None)
            if attribute["kind"] in {"allow", "expect"}
        )
    return found
