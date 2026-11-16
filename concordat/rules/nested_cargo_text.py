"""Split Rust call arguments on the commas that actually separate them.

A comma inside a quoted value is data, not a separator, so the quote has to be
tracked rather than left to the caller. Both the command reader and the module
reader need this rule, and neither owns the other's command model, so it lives
here on its own.
"""

from __future__ import annotations

from .rust_source import scan_balanced


def split_top_level(text: str) -> list[str]:
    """Split on commas outside nested parentheses and string literals.

    A comma inside a quoted value is data, not a separator. Splitting on it
    turns `.env("RUST_LOG", "info,foo=debug")` — one name-value pair — into
    three slots, which cannot be read as pairs at all. The command is then
    reported `unresolved` and the NC-007 finding its real flags earned is
    hidden, so the quote is tracked here rather than left to the caller.

    Returns
    -------
    list[str]
        The comma-separated parts, in source order.
    """
    parts: list[str] = []
    depth, start = 0, 0
    index = 0
    is_quoted = False
    while index < len(text):
        char = text[index]
        if is_quoted:
            # A backslash escapes the next character, so the quote after it
            # does not close the literal: `"a\"b"` is one value.
            if char == "\\":
                index += 2
                continue
            if char == '"':
                is_quoted = False
        elif char == '"':
            is_quoted = True
        else:
            depth += (char == "(") - (char == ")")
            if char == "," and depth == 0:
                parts.append(text[start:index])
                start = index + 1
        index += 1
    parts.append(text[start:])
    return parts


def argument_items(text: str) -> list[str]:
    """Return the arguments one `.arg(..)`/`.args(..)` call passes, as source text.

    `.args(["--target-dir", dir])` and `.arg("--target-dir")` both describe a
    word sequence; the brackets of an `.args` list are not part of any word.

    Returns
    -------
    list[str]
        The non-blank arguments in source order.
    """
    return [item.strip() for item in split_top_level(_unwrap(text)) if item.strip()]


def environment_slots(text: str) -> list[tuple[str, str | None]] | None:
    """Return a `.env`/`.envs` argument list as raw name and value expressions.

    The offline ledger reads only the literals; the target-directory reader
    needs the value's source text too, so the split lives here once.

    Returns
    -------
    list[tuple[str, str | None]] | None
        One `(name, value)` pair of source text per variable, the value None
        when the group has no second slot, or None when the argument list is
        not a readable list of pairs at all.
    """
    groups = _top_level_groups(text)
    if groups is None:
        return None
    pairs: list[tuple[str, str | None]] = []
    for group in groups:
        slots = split_top_level(_unwrap(group))
        # rustfmt adds a trailing comma when it breaks a call over several
        # lines, so `.env(\n    "K",\n    "V",\n)` arrives as three slots with
        # a blank last one. Counting that as a third argument rejects a pair
        # the reader can see perfectly well, which both hides real offline
        # evidence and reports the command `unresolved`.
        if slots and not slots[-1].strip():
            slots.pop()
        if len(slots) > 2:
            return None
        pairs.append((slots[0] if slots else "", slots[1] if len(slots) == 2 else None))
    return pairs


def _unwrap(text: str) -> str:
    """Return *text* with one enclosing bracket pair and any `&` removed.

    `("K", "V")`, `["K", "V"]` and `&("K", "V")` all describe the same pair.
    Anything else is returned unchanged.

    Returns
    -------
    str
        The text inside the brackets, or *text* itself.
    """
    stripped = text.strip().removeprefix("&").strip()
    if not stripped or stripped[0] not in "([":
        return stripped
    close = scan_balanced(stripped, 0)
    if close is None or stripped[close - 1] != ("]" if stripped[0] == "[" else ")"):
        return stripped
    if stripped[close:].strip():
        return stripped
    return stripped[1 : close - 1]


def _top_level_groups(text: str) -> list[str] | None:
    """Split an argument list into its outermost name-value groups.

    `"K", "V"` is one group. `[("K", "V"), ("J", "W")]` is two. A group may
    still turn out to be unreadable, which the caller decides slot by slot.

    Returns
    -------
    list[str] | None
        The groups, or None when the bracket structure does not balance or the
        list is followed by more expression.
    """
    stripped = text.strip().removeprefix("&").strip()
    if not stripped:
        return []
    if stripped[0] != "[":
        return [stripped]
    close = scan_balanced(stripped, 0)
    if close is None:
        return None
    # Text after the closing bracket may add pairs the reader cannot see:
    # `.envs([("CARGO_NET_OFFLINE", "true")].into_iter().chain(extra))` runs
    # `extra`'s pairs after the visible one, and one of them may set the
    # variable back. Reading only the literal list would report `offline` on a
    # guess, so the whole argument is unreadable.
    if stripped[close:].strip():
        return None
    return [
        group for group in split_top_level(stripped[1 : close - 1]) if group.strip()
    ]
