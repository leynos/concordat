"""Read a tool-path variable: every alternative ends in the tool's own name.

A Makefile often finds a tool by searching, then falling back:

    CARGO ?= $(or $(shell command -v cargo 2>/dev/null),$(HOME)/.cargo/bin/cargo)
    UV ?= $(if $(wildcard $(HOME)/.local/bin/uv),$(HOME)/.local/bin/uv,uv)

The expansion cannot follow `$(shell ...)` or `$(wildcard ...)`, so `$(CARGO)
test` stays unresolved. But when every alternative the value can take names the
same tool (the last path component of each is `cargo`), then whichever one Make
picks, the recipe runs `cargo`, and the variable can be read as that name.

The reading is deliberately narrow. The variable must be named for the tool
(`CARGO` for `cargo`), and its value must be a search built from:

* a path word (letters, digits, `_`, `.`, `/`, `-`, plain `$(NAME)` references
  in its directory part, no glob characters) whose last component is the name;
* `$(shell command -v NAME)`, optionally with `2>/dev/null`, and no other shell;
* `$(wildcard PATH)` with a path word as its argument;
* `$(or ...)` over qualifying alternatives;
* `$(if COND,THEN,ELSE)` over qualifying branches, whose condition is a
  `$(wildcard PATH)` or text free of side effects.

Anything else (another shell command, a glob, a branch that is empty, an `if`
without an else, alternatives naming different tools) leaves the variable
unresolved, which is the safe reading.
"""

from __future__ import annotations

import re
import typing as typ

from .makefile_expansion import (
    EXPANSION_PASSES,
    Assignments,
    close_of,
    is_pure,
    top_level_commas,
)

if typ.TYPE_CHECKING:
    import collections.abc as cabc

_CALL: typ.Final = re.compile(r"\$\((or|if|shell|wildcard)\s+")
_NAME: typ.Final = re.compile(r"[A-Za-z][A-Za-z0-9_-]*")
_PATH_WORD: typ.Final = re.compile(r"[A-Za-z0-9_./$()-]+")
_REFERENCE: typ.Final = re.compile(r"\$\(([A-Za-z_][A-Za-z0-9_]*)\)")
_SHELL_BODY: typ.Final = re.compile(
    r"command\s+-v\s+(?P<name>[A-Za-z][A-Za-z0-9_-]*)(?:\s+2>/dev/null)?"
)


def _path_name(text: str, known: cabc.Mapping[str, str]) -> str | None:
    """Return the last component of a path word, or None when it is not one.

    Returns
    -------
    str | None
        The tool name the word ends in. Variables the Makefile assigns are
        substituted first; a reference left in the directory part is data from
        the environment (`$(HOME)`), but one in the last component is not.
    """
    word = text.strip()
    for _ in range(EXPANSION_PASSES):
        updated = _REFERENCE.sub(lambda m: known.get(m.group(1), m.group(0)), word)
        if updated == word:
            break
        word = updated
    if _PATH_WORD.fullmatch(word) is None:
        return None
    directory, _, last = word.rpartition("/")
    if _REFERENCE.sub("", directory).count("$") or "(" in _REFERENCE.sub("", directory):
        return None
    return last if _NAME.fullmatch(last) else None


def _call_body(text: str) -> tuple[str, str] | None:
    """Return the function and body of *text* when it is one whole call."""
    match = _CALL.match(text)
    if match is None or close_of(text, 1) != len(text) - 1:
        return None
    return match.group(1), text[match.end() : -1]


def _single(names: list[str | None]) -> str | None:
    """Return the one name every alternative agrees on, or None."""
    if names and None not in names and len(set(names)) == 1:
        return names[0]
    return None


def _condition_is_safe(condition: str, known: Assignments) -> bool:
    """Return whether an `if` condition only tests text or file existence."""
    call = _call_body(condition.strip())
    if call is not None and call[0] == "wildcard":
        return _path_name(call[1], known) is not None
    return is_pure(condition, known, known.assigned)


def _alternative(text: str, known: Assignments) -> str | None:
    """Return the tool name every value *text* can take ends in, or None."""
    text = text.strip()
    call = _call_body(text)
    if call is None:
        return _path_name(text, known)
    function, body = call
    commas = top_level_commas(body)
    parts = [
        body[start:end]
        for start, end in zip(
            [0, *(i + 1 for i in commas)], [*commas, len(body)], strict=True
        )
    ]
    match function:
        case "or":
            return _single([_alternative(part, known) for part in parts])
        case "if" if len(parts) == 3 and _condition_is_safe(parts[0], known):
            return _single([_alternative(part, known) for part in parts[1:]])
        case "shell":
            shell = _SHELL_BODY.fullmatch(body.strip())
            return shell.group("name") if shell is not None else None
        case "wildcard":
            return _path_name(body, known)
        case _:
            return None


def _read(name: str, value: str, known: Assignments) -> str:
    """Return the tool name a variable called *name* reads as, or its own value.

    Only a search (a call) is read, and only for a variable named for the tool
    it finds (`CARGO` for `cargo`): a plain path variable keeps its path, which
    a recipe may use as a path.

    Returns
    -------
    str
        The tool name, or *value* unchanged.
    """
    if _call_body(value.strip()) is None:
        return value
    tool = _alternative(value, known)
    return tool if tool is not None and tool == name.lower() else value


def read_tool_paths(values: Assignments) -> Assignments:
    """Return *values* with each tool-path variable replaced by its tool name.

    Returns
    -------
    Assignments
        The same assignments, where a variable named for a tool, whose value
        searches and whose every alternative ends in that tool's name, now
        holds the name. Other variables are unchanged.
    """
    resolved = {name: _read(name, value, values) for name, value in values.items()}
    return Assignments(resolved, values.assigned)
