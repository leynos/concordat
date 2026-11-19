"""Read a Rust signature and body by bracket depth, without a parser.

The nested-cargo spawn reader finds a helper's parameters and body in text, so
it has to tell `,`, `;` and brackets that structure the item from the same
characters inside a type. Generics hold commas (`Result<A, B, E>`), arrays hold
semicolons (`[u8; 4]`), const blocks hold comparisons (`Flag<{ 1 < 2 }>`), and
a return arrow's `>` closes no generic. Each question here is answered against
one depth scan (:func:`_depths`) that tracks which opener is still unclosed, so
`>` never closes a `]` and a brace at the top level is the item's own body.

A brace inside a const block is read as depth and not as structure, and inside
a block only braces are read, because the scan's callers only ever look at a
signature or an item's first delimiter.
"""

from __future__ import annotations

import re
import typing as typ

# A `self` receiver makes the parameter the command reaches ambiguous, so the
# callee analysis is declined rather than guessed.
SELF_RECEIVER: typ.Final = re.compile(r"\s*(?:&\s*(?:'\w+\s*)?)?(?:mut\s+)?self\b")
# Only a plain `[mut] name: Type` parameter names a value the helper can use.
PARAMETER: typ.Final = re.compile(
    r"\s*(?:mut\s+)?(?P<name>[A-Za-z_]\w*)\s*:\s*\S[\s\S]*"
)


def _join(depth: tuple[str, ...], closer: str) -> tuple[str, ...]:
    """Return the bracket depth left after reading *closer*.

    Each bracket has its own opener, so a `>` does not close a `]` and a
    comparison inside a const block cannot be mistaken for a generic.

    Returns
    -------
    tuple[str, ...]
        The depth with the matching opener removed, or unchanged when openers
        remain, or the unbalanced marker when *closer* matches nothing.
    """
    opened = _OPENER.get(closer)
    if opened is None or not depth:
        return depth
    if depth[-1] == opened:
        return depth[:-1]
    opener = depth.index(opened) if opened in depth else None
    return (_UNBALANCED,) if opener is None else depth[:opener]


_OPENER: typ.Final = {")": "(", "]": "[", ">": "<"}
# Marks a closer that matches nothing; no opener uses this string.
_UNBALANCED: typ.Final = "!"


def _depths(masked: str, start: int) -> typ.Iterator[tuple[int, tuple[str, ...]]]:
    """Yield each offset from *start* with the bracket depth before it.

    A const block (`Tag<{ 1 + 1 }>`) counts as one level, and inside it only
    braces are read, so a `<` there is a comparison and not a generic opener.
    A brace at the top level is the item's own body and is reported at depth
    zero rather than entered. A return arrow is skipped whole.

    Yields
    ------
    tuple[int, tuple[str, ...]]
        Each offset, with the openers still unclosed before it.
    """
    depth: tuple[str, ...] = ()
    index = start
    while index < len(masked):
        yield index, depth
        char = masked[index]
        if masked.startswith("->", index):
            index += 2
            continue
        inside_brace = bool(depth) and depth[-1] == "{"
        if char == "{":
            if depth:
                depth += ("{",)
        elif char == "}":
            if inside_brace:
                depth = depth[:-1]
        elif char in "([<" and not inside_brace:
            depth += (char,)
        elif char in ")]>" and not inside_brace:
            depth = _join(depth, char)
        index += 1


def top_level_item(masked: str, start: int) -> tuple[int, str] | None:
    """Return the first `{` or `;` after *start* that sits outside any bracket.

    A signature can hold a `;` inside an array length (`[u8; 4]`), and generics
    can hold a brace in a const argument (`Tag<{ 1 + 1 }>`), so only a
    delimiter at depth zero ends the item. A stray closing bracket or the end
    of the file leaves the item unfinished.

    Returns
    -------
    tuple[int, str] | None
        The offset and the character found, or None when the item does not
        close cleanly.
    """
    for index, depth in _depths(masked, start):
        if depth == (_UNBALANCED,):
            return None
        if depth:
            continue
        match masked[index]:
            case "{" | ";":
                return index, masked[index]
            case "}":
                return None
    return None


def _parameter_open(masked: str, start: int) -> int | None:
    """Return the offset of the `(` that opens a parameter list after *start*.

    Generics may hold their own parentheses (`F: Fn()`) and return arrows
    (`F: Fn() -> ()`), so only a `(` at bracket depth zero opens the parameter
    list, and the `>` of an arrow does not close a generic.

    Returns
    -------
    int | None
        The offset of the `(`, or None when none follows at depth zero.
    """
    for index, depth in _depths(masked, start):
        if depth == (_UNBALANCED,):
            return None
        if masked[index] == "(" and not depth:
            return index
    return None


def _parameter_close(masked: str, open_at: int) -> int | None:
    """Return the offset of the `)` that closes the parameter list at *open_at*.

    Returns
    -------
    int | None
        The offset of the closing `)`, or None when the list never closes.
    """
    depth = 0
    for index in range(open_at, len(masked)):
        match masked[index]:
            case "(":
                depth += 1
            case ")":
                depth -= 1
                if depth == 0:
                    return index
    return None


def _split_parameters(parameters: str) -> list[str]:
    """Split a parameter list on the commas that sit outside brackets.

    A generic type (`Result<A, B, E>`), an array length (`[u8; 4]`) or a
    function-pointer return (`fn() -> ()`) keeps its commas, so a position
    still indexes the parameter the caller passed. The `>` of a return arrow
    is not a generic closer, and a comparison inside a const block
    (`Flag<{ 1 < 2 }>`) is not a generic opener.

    Returns
    -------
    list[str]
        The parameters in order; an empty trailing entry is kept so positions
        do not shift.
    """
    parts: list[str] = []
    start = 0
    for index, depth in _depths(parameters, 0):
        if parameters[index] == "," and not depth:
            parts.append(parameters[start:index])
            start = index + 1
    parts.append(parameters[start:])
    return parts


def _parameter_at(
    masked: str, definition_end: int, position: int
) -> tuple[str, int] | None:
    """Return the parameter at *position* and the offset closing its list.

    Only a plain `[mut] name: Type` parameter names the command the helper
    receives. A `self` receiver or a more elaborate parameter hides which
    value the command reaches, so the callee is declined instead of guessed.

    Returns
    -------
    tuple[str, int] | None
        The parameter's name and the offset of the closing `)`, or None when
        the list cannot be read or the position holds no simple parameter.
    """
    open_at = _parameter_open(masked, definition_end)
    if open_at is None:
        return None
    close_at = _parameter_close(masked, open_at)
    if close_at is None:
        return None
    parameters = masked[open_at + 1 : close_at]
    if SELF_RECEIVER.match(parameters):
        return None
    parts = _split_parameters(parameters)
    if position >= len(parts):
        return None
    found = PARAMETER.fullmatch(parts[position])
    return None if found is None else (found["name"], close_at)


def _body_span(
    masked: str, closers: dict[int, int], start: int
) -> tuple[int, int] | None:
    """Return the body offsets of a function whose signature ends at *start*.

    The body is the matched brace pair, so a `;` in the signature or a brace
    in a nested item cannot end it early.

    Returns
    -------
    tuple[int, int] | None
        The offsets just inside the braces, or None for a declaration (`fn
        f(..);`) or an unbalanced body.
    """
    item = top_level_item(masked, start)
    if item is None or item[1] != "{":
        return None
    end = closers.get(item[0])
    return None if end is None else (item[0] + 1, end - 1)


def parameter_names(masked: str, start: int) -> list[str]:
    """Return the plain `name: Type` parameters of the function after *start*.

    A caller's own parameter shadows a free function of the same name, so the
    caller's list is read at the call site. A `self` receiver hides which value
    the command reaches, so a method's list is not read at all.

    Returns
    -------
    list[str]
        The names of the plain parameters, in order; empty when the list cannot
        be read or holds a receiver.
    """
    open_at = _parameter_open(masked, start)
    if open_at is None:
        return []
    close_at = _parameter_close(masked, open_at)
    if close_at is None:
        return []
    parameters = masked[open_at + 1 : close_at]
    if SELF_RECEIVER.match(parameters):
        return []
    names: list[str] = []
    for part in _split_parameters(parameters):
        found = PARAMETER.fullmatch(part)
        if found is not None:
            names.append(found["name"])
    return names
