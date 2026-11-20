"""Read a Rust signature and body by bracket depth, without a parser.

The nested-cargo spawn reader finds a helper's parameters and body in text, so
it has to tell `,`, `;` and brackets that structure the item from the same
characters inside a type. Generics hold commas (`Result<A, B, E>`), arrays hold
semicolons (`[u8; 4]`), const blocks hold comparisons (`Flag<{ 1 < 2 }>`), and
a return arrow's `>` closes no generic. Each question here is answered against
one depth scan (:func:`_depths`) that counts the openers still unclosed, so `>`
closes only a `<` and a brace at the top level is the item's own body.

A brace inside a const block is read as depth and not as structure, and inside
a block only braces are read, because the scan's callers only ever look at a
signature or an item's first delimiter.

A parameter that is a pattern rather than a plain `name: Type` is read for the
names it binds, because `fn run((configure,): ..)` binds `configure` in the
caller's scope just as a `let` does. A struct pattern's field colon
(`Callbacks { setup: configure }: ..`) is not the delimiter before the type,
and a pattern's own commas do not split the parameter from its neighbour.
"""

from __future__ import annotations

import re
import typing as typ

# A `self` receiver makes the parameter the command reaches ambiguous, so a
# helper's own list is declined rather than guessed. A caller's list is still
# read past it, because a later parameter can shadow a helper name.
SELF_RECEIVER: typ.Final = re.compile(r"\s*(?:&\s*(?:'\w+\s*)?)?(?:mut\s+)?self\b")
# Only a plain `[mut] name: Type` parameter names a value the helper can use.
PARAMETER: typ.Final = re.compile(
    r"\s*(?:mut\s+)?(?P<name>[A-Za-z_]\w*)\s*:\s*\S[\s\S]*"
)


_OPENER: typ.Final = {")": "(", "]": "["}
# `pub(crate)` is a parenthesis that holds no parameter, so it is not the list.
_VISIBILITY: typ.Final = re.compile(r"\bpub\s*$")


def _depths(
    masked: str, start: int, *, enter_braces: bool = False
) -> typ.Iterator[tuple[int, int | None]]:
    """Yield each offset from *start* with the number of openers before it.

    A const block (`Tag<{ 1 + 1 }>`) counts as one level, and inside it only
    braces are read, so a `<` there is a comparison and not a generic opener.
    A brace at the top level is the item's own body and is reported at depth
    zero rather than entered, unless *enter_braces* asks for it: in a parameter
    list a brace is a struct pattern or a const block, never a body, so its
    field commas must stay inside the parameter. A return arrow is skipped
    whole.

    A `>` closes a `<` only when the `<` is still the innermost opener, so the
    `>` of an array length's shift (`[u8; 4 >> 1]`, and `Option<[u8; 4 >> 1]>`
    around it) is an operator rather than a closer. A `)` or `]` that opens
    nothing ends the scan with an unknown depth, because nothing after it can
    be trusted.

    Yields
    ------
    tuple[int, int | None]
        Each offset, with the openers still unclosed before it, or ``None``
        when a closer matched nothing.
    """
    openers: list[str] = []
    index = start
    while index < len(masked):
        yield index, len(openers)
        char = masked[index]
        if masked.startswith("->", index):
            index += 2
            continue
        inside_brace = bool(openers) and openers[-1] == "{"
        if char == "{":
            if openers or enter_braces:
                openers.append("{")
        elif char == "}":
            if inside_brace:
                openers.pop()
        elif char in "([<" and not inside_brace:
            openers.append(char)
        elif char == ">" and not inside_brace:
            # Only an innermost `<` is a generic that `>` can close. A `[` or
            # `(` opened inside one is still open here, so the `>` belongs to a
            # shift in an array length or a comparison, not to a type.
            if openers and openers[-1] == "<":
                openers.pop()
        elif char in ")]" and not inside_brace:
            opened = _OPENER[char]
            if opened in openers:
                while openers[-1] != opened:
                    openers.pop()
                openers.pop()
            else:
                yield index, None
                return
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
        if depth is None:
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
    list, and the `>` of an arrow does not close a generic. A restricted
    visibility (`pub(crate) fn run(..)`) is a parenthesis of its own, so the
    one after `pub` is skipped rather than read as the parameter list.

    Returns
    -------
    int | None
        The offset of the `(`, or None when none follows at depth zero.
    """
    for index, depth in _depths(masked, start):
        if depth is None:
            return None
        if masked[index] != "(" or depth:
            continue
        if _VISIBILITY.search(masked, start, index) is None:
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

    A generic type (`Result<A, B, E>`), an array length (`[u8; 4]`), a
    function-pointer return (`fn() -> ()`) or a struct pattern
    (`Pair { first, command: other }`) keeps its commas, so a position still
    indexes the parameter the caller passed. The `>` of a return arrow is not a
    generic closer, a comparison inside a const block (`Flag<{ 1 < 2 }>`) is
    not a generic opener, and the `>>` of an array length (`[u8; 4 >> 1]`) is a
    shift, not a closer.

    Returns
    -------
    list[str]
        The parameters in order; an empty trailing entry is kept so positions
        do not shift.
    """
    parts: list[str] = []
    start = 0
    for index, depth in _depths(parameters, 0, enter_braces=True):
        if parameters[index] == "," and depth == 0:
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


def _binding_pattern(part: str) -> str:
    """Return the binding a parameter writes before its type delimiter.

    A struct pattern's first colon belongs to a field
    (`Callbacks { setup: configure }: Callbacks`), so the delimiter is the
    first `:` that no bracket holds. A `::` is a path separator, not the
    delimiter, however it is spaced.

    Returns
    -------
    str
        The parameter's binding text, or the whole parameter when it holds no
        delimiter.
    """
    depth = 0
    for index, char in enumerate(part):
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth = max(depth - 1, 0)
        elif char == ":" and depth == 0:
            before = part[index - 1] if index else ""
            after = part[index + 1] if index + 1 < len(part) else ""
            if before != ":" and after != ":":
                return part[:index]
    return part


def pattern_binds(masked: str, start: int, name: str) -> bool:
    """Report whether a parameter pattern binds *name*.

    A tuple or struct pattern (`fn run((configure,): (fn(&mut Command),))`)
    binds names the plain reader cannot see. When one of them is a helper's
    name, the caller's scope is uncertain and the handoff is declined rather
    than attributed to a same-file function. Only the text before the
    parameter's type delimiter is a binding pattern; the type after it names no
    value, and a struct pattern's field colon is not that delimiter.

    Returns
    -------
    bool
        True when the list can be read and a non-plain parameter's pattern
        mentions *name*.
    """
    open_at = _parameter_open(masked, start)
    if open_at is None:
        return False
    close_at = _parameter_close(masked, open_at)
    if close_at is None:
        return False
    parameters = masked[open_at + 1 : close_at]
    parts = _split_parameters(parameters)
    if SELF_RECEIVER.match(parameters):
        parts = parts[1:]
    bound = re.compile(rf"\b{re.escape(name)}\b")
    for part in parts:
        if not part.strip() or PARAMETER.fullmatch(part) is not None:
            continue
        if bound.search(_binding_pattern(part)) is not None:
            return True
    return False


def parameter_names(masked: str, start: int) -> list[str]:
    """Return the plain `name: Type` parameters of the function after *start*.

    A caller's own parameter shadows a free function of the same name, so the
    caller's list is read at the call site. A `self` receiver names the value
    the method is called on, not a callback, so it is skipped rather than
    ending the list; a named parameter after it still shadows a helper.

    Returns
    -------
    list[str]
        The names of the plain parameters, in order; empty when the list cannot
        be read or holds only a receiver.
    """
    open_at = _parameter_open(masked, start)
    if open_at is None:
        return []
    close_at = _parameter_close(masked, open_at)
    if close_at is None:
        return []
    parameters = masked[open_at + 1 : close_at]
    parts = _split_parameters(parameters)
    if SELF_RECEIVER.match(parameters):
        parts = parts[1:]
    names: list[str] = []
    for part in parts:
        found = PARAMETER.fullmatch(part)
        if found is not None:
            names.append(found["name"])
    return names
