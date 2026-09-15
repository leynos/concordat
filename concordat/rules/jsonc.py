"""Decode JSON with Comments (JSONC) documents such as `.markdownlint-cli2.jsonc`.

markdownlint-cli2 reads its configuration through a JSONC parser that accepts
`//` and `/* */` comments and trailing commas. The standard library's `json`
accepts neither, so a faithful reading of a repository's configuration needs
the relaxations stripped first. Comments and commas are only removed outside
string literals; the remaining text is then decoded as strict JSON, so any
other deviation from JSON is still reported.
"""

from __future__ import annotations

import json
import typing as typ


class JsoncError(ValueError):
    """The document is not valid JSONC."""


def _skip_string(text: str, index: int, out: list[str]) -> int:
    """Copy the string literal starting at *index* and return the index after it.

    A backslash escapes the following character, so an escaped quote does not
    end the literal. An unterminated literal is copied to the end of the text
    and left for the JSON decoder to reject.

    Returns
    -------
    int
        Index of the first character after the literal.
    """
    out.append(text[index])
    index += 1
    while index < len(text):
        char = text[index]
        out.append(char)
        index += 1
        if char == "\\" and index < len(text):
            out.append(text[index])
            index += 1
        elif char == '"':
            break
    return index


def _skip_line_comment(text: str, index: int) -> int:
    """Return the index of the newline that ends the `//` comment at *index*."""
    end = text.find("\n", index)
    return len(text) if end == -1 else end


def _skip_block_comment(text: str, index: int) -> int:
    """Return the index after the `*/` closing the block comment at *index*.

    Returns
    -------
    int
        Index of the first character after the closing `*/`.

    Raises
    ------
    JsoncError
        If the block comment never closes.
    """
    end = text.find("*/", index + 2)
    if end == -1:
        message = "unterminated block comment"
        raise JsoncError(message)
    return end + 2


def _strip_comments(text: str) -> str:
    """Return *text* with every comment outside a string literal removed.

    Line comments are replaced by nothing but the newline that ended them,
    so the line numbering the JSON decoder reports in errors still matches
    the original document.

    Returns
    -------
    str
        The text without comments.
    """
    out: list[str] = []
    index = 0
    while index < len(text):
        char = text[index]
        pair = text[index : index + 2]
        if char == '"':
            index = _skip_string(text, index, out)
        elif pair == "//":
            index = _skip_line_comment(text, index)
        elif pair == "/*":
            index = _skip_block_comment(text, index)
        else:
            out.append(char)
            index += 1
    return "".join(out)


def _strip_trailing_commas(text: str) -> str:
    """Return *text* with commas that directly precede `}` or `]` removed.

    Only commas outside string literals are considered, and only whitespace
    may separate the comma from the closing bracket.

    Returns
    -------
    str
        The text without trailing commas.
    """
    out: list[str] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char == '"':
            index = _skip_string(text, index, out)
            continue
        if char == ",":
            lookahead = index + 1
            while lookahead < len(text) and text[lookahead].isspace():
                lookahead += 1
            if lookahead < len(text) and text[lookahead] in "}]":
                index += 1
                continue
        out.append(char)
        index += 1
    return "".join(out)


def loads_jsonc(text: str) -> object:
    """Decode a JSONC document and return the resulting value.

    Parameters
    ----------
    text:
        The document text.

    Returns
    -------
    object
        The decoded value, exactly as :func:`json.loads` would return it.

    Raises
    ------
    JsoncError
        If the document is not valid JSONC.
    """
    try:
        stripped = _strip_trailing_commas(_strip_comments(text))
        return typ.cast("object", json.loads(stripped))
    except json.JSONDecodeError as error:
        message = (
            f"invalid JSON at line {error.lineno} column {error.colno}: {error.msg}"
        )
        raise JsoncError(message) from error
