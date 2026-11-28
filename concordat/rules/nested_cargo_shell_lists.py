"""Read the `make` calls a recipe line always runs before its tests start.

A recipe line is a shell command line. Whether a `make <goal>` on it has
finished by the time the tests start depends on the shell's operators, so the
line is tokenised the way a shell would, with quotes respected, rather than
searched for text:

- a call counts only as the leading run of plain `make <goal>` commands of an
  `&&` list, because a part that is not a `make` call may fail and skip the
  rest;
- a list ended by `&` is backgrounded, so none of it is done before what
  follows; one ended by `||` or `|` may be skipped or hide its status, and none
  of its calls is counted;
- a list after `||`, a subshell and a shell branch may not run at all;
- a command that runs a test command, or a `make` call the reader cannot name
  (an option, an assignment or a variable goal), ends the scan.
"""

from __future__ import annotations

import dataclasses
import re
import shlex
import typing as typ

_PLAIN_GOAL: typ.Final = re.compile(r"[A-Za-z_.][\w.-]*")
_TEST_COMMAND: typ.Final = re.compile(r"\b(?:cargo|nextest|pytest)\b")
_OPERATOR: typ.Final = re.compile(r"[();<>|&]+")
_RECIPE_PREFIXES: typ.Final = "@+-"
_CONTROL_WORDS: typ.Final = frozenset({
    "if",
    "then",
    "else",
    "elif",
    "fi",
    "case",
    "esac",
    "while",
    "for",
    "until",
    "do",
    "done",
})
_SEQUENCE_ENDS: typ.Final = frozenset({"", ";"})


@dataclasses.dataclass(slots=True)
class _List:
    """Hold one `&&` list of a command line, and the operator that ends it."""

    commands: list[list[str]] = dataclasses.field(default_factory=list)
    end: str = ""
    follows_or: bool = False


def _tokens(text: str) -> list[str] | None:
    """Return a recipe line's shell tokens, or None when it cannot be read."""
    # Not POSIX mode: a quoted word keeps its quotes, so a quoted `;` is never
    # taken for the operator. A backslash escape is not modelled, so a line
    # holding one cannot be read.
    if "\\" in text:
        return None
    lexer = shlex.shlex(
        text.replace("$(MAKE)", "make").replace("${MAKE}", "make"),
        posix=False,
        punctuation_chars=True,
    )
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
        return list(lexer)
    except ValueError:
        return None


def _lists(tokens: list[str]) -> list[_List]:
    """Group tokens into `&&` lists, each with the operator that ends it.

    Returns
    -------
    list[_List]
        The lists in order. An operator other than `&&` ends the current list.
    """
    lists = [_List()]
    words: list[str] = []
    for word in tokens:
        if _OPERATOR.fullmatch(word) is None:
            words.append(word)
            continue
        lists[-1].commands.append(words)
        words = []
        if word != "&&":
            lists[-1].end = word
            lists.append(_List(follows_or=word == "||"))
    lists[-1].commands.append(words)
    return lists


def _plain_goal(words: list[str]) -> str | None:
    """Return the goal of a plain `make <goal>` command, or None."""
    if len(words) != 2 or words[0].lstrip(_RECIPE_PREFIXES) != "make":
        return None
    return words[1] if _PLAIN_GOAL.fullmatch(words[1]) else None


def _starts_tests(words: list[str]) -> bool:
    """Report whether a command runs a test command or an unnamed `make` goal."""
    is_make = bool(words) and words[0].lstrip(_RECIPE_PREFIXES) == "make"
    if is_make and _plain_goal(words) is None:
        return True
    return _TEST_COMMAND.search(" ".join(words)) is not None


def _leading_goals(commands: list[list[str]]) -> list[str]:
    """Return the plain `make` calls that lead an `&&` list, in order."""
    goals: list[str] = []
    for words in commands:
        goal = _plain_goal(words)
        if goal is None:
            break
        goals.append(goal)
    return goals


def _is_unreadable(tokens: list[str]) -> bool:
    """Report whether a line holds a subshell or a shell branch."""
    return "(" in tokens or bool(_CONTROL_WORDS & set(tokens))


def _always_runs(each: _List) -> bool:
    """Report whether a list runs in the foreground and is not skipped."""
    return each.end in _SEQUENCE_ENDS and not each.follows_or


def leading_make_goals(text: str) -> list[str]:
    """Return the goals one recipe line always calls before its tests can start.

    Returns
    -------
    list[str]
        The goals, in order. A line the reader cannot tokenise, one with a
        subshell or a shell branch, contributes none.
    """
    tokens = _tokens(text)
    if tokens is None or _is_unreadable(tokens):
        return []
    found: list[str] = []
    for each in _lists(tokens):
        if _always_runs(each):
            found.extend(_leading_goals(each.commands))
        if any(_starts_tests(words) for words in each.commands):
            break
    return found
