"""Split Rust call arguments on the commas that actually separate them.

A comma inside a quoted value is data, not a separator, so the quote has to be
tracked rather than left to the caller. Both the command reader and the module
reader need this rule, and neither owns the other's command model, so it lives
here on its own.
"""

from __future__ import annotations


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
