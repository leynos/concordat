"""Find the patterns and items that hide a helper's name at a call site.

The handoff reader splices a helper's body in at the call, which is only right
when the name written there is the free function. A `match` arm's pattern
binds every name it mentions, so `match callback { configure => configure(..) }`
calls the bound value; the scope ends with the arm's body. The readers here
locate the arms and the blocks that hold a call, because both questions are
answered by the same brace walk: which blocks enclose the call, and which of
them is a match.

Blocks are identified by the offset of their opening brace rather than by their
depth, so two sibling blocks at the same depth are told apart. A sibling block
that has closed does not enclose the call, which is what keeps its pattern or
item out of the answer.
"""

from __future__ import annotations

import re
import typing as typ

# An arm arrow and the brackets that hold a pattern together. A pattern's own
# comma (`(configure, other) =>`) is inside its parentheses, so only a comma at
# the arm's own level ends one.
_ARM_TOKENS: typ.Final = re.compile(r"=>|[()\[\]{},]")


def _arm_pattern_binds(segment: str, name: str) -> bool:
    """Report whether an arm pattern in *segment* binds *name*.

    Only text before an `=>` that sits outside brackets is a pattern; an
    arrow deeper in an arm's block belongs to a match the arm body holds. The
    pattern is the text since the arm's start, which a comma at the arm's own
    level ends, so a preceding sibling arm's body is not read as part of it.

    Returns
    -------
    bool
        True when a pattern mentions *name* as a whole word.
    """
    bound = re.compile(rf"\b{re.escape(name)}\b")
    depth = 0
    arm_start = 0
    for found in _ARM_TOKENS.finditer(segment):
        arm_char = found.group()
        if arm_char in "([{":
            depth += 1
        elif arm_char in ")]}":
            depth = max(depth - 1, 0)
        elif depth:
            continue
        elif arm_char == "=>":
            if bound.search(segment[arm_start : found.start()]) is not None:
                return True
            arm_start = found.end()
        else:
            arm_start = found.end()
    return False


def match_arm_binds(masked: str, name: str, start: int, at: int) -> bool:
    """Report whether the match arm holding *at* binds *name*.

    A pattern binds every name it mentions, so a call written with that name
    reaches the bound value rather than the free function:
    `match callback { configure => configure(&mut command) }` calls the
    callback. The blocks open at the call are read from the innermost out,
    because the arm's body may be a block of its own: the match brace is the
    one whose text holds the arm's `=>`.

    Returns
    -------
    bool
        True when an arm pattern enclosing *at* mentions *name*.
    """
    blocks = open_braces(masked, start, at)
    return any(
        _arm_pattern_binds(masked[brace + 1 : at], name) for brace in reversed(blocks)
    )


def open_braces(masked: str, base: int, at: int) -> list[int]:
    """Return the offsets of the braces open at *at*, scanning from *base*.

    The offsets identify the blocks rather than their depth, so two sibling
    blocks that sit at the same depth are told apart.

    Returns
    -------
    list[int]
        The opening braces still unclosed at *at*, outermost first.
    """
    openers: list[int] = []
    for index in range(base, min(at, len(masked))):
        char = masked[index]
        if char == "{":
            openers.append(index)
        elif char == "}" and openers:
            openers.pop()
    return openers
