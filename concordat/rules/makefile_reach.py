"""Walk a Makefile from a gate target to every target and recipe it reaches.

Several rules ask what `make lint` (or `make test`) really runs, so they follow
a target through its prerequisites and the `make name` commands in its recipes.
This module is that walk, shared: it indexes the rules a `makeutil parse` report
holds, and from a set of starting targets returns every target reached and
whether the walk could name each one it met. A prerequisite or `make` goal that
names a variable the expansion cannot settle is skipped and clears the resolved
flag, so a caller never reads a partial walk as a complete one.
"""

from __future__ import annotations

import collections
import dataclasses
import re
import typing as typ

from .interrogate_invocation import command_words, lex_segments
from .makefile_expansion import UNRESOLVED_MARK, expand

if typ.TYPE_CHECKING:
    import collections.abc as cabc

    from .makefile_facts import MakeutilReport

__all__ = [
    "Rule",
    "RuleIndex",
    "Successors",
    "reachable",
    "rules_by_target",
]


class Rule(typ.NamedTuple):
    """One Make rule: its prerequisites and its `(line, text)` recipe lines.

    `is_conditional` is true when the rule sits inside an `ifeq`, `ifdef` or
    similar block, so whether it applies depends on Make state.
    """

    prerequisites: list[str]
    recipes: list[tuple[int, str]]
    is_conditional: bool = False


type RuleIndex = dict[str, list[Rule]]


@dataclasses.dataclass(frozen=True, slots=True)
class Successors:
    """The targets a rule leads to, and whether every one could be named."""

    names: list[str]
    is_resolved: bool


def rules_by_target(report: MakeutilReport) -> RuleIndex:
    """Index rules by target, in file order.

    Returns
    -------
    RuleIndex
        Every rule that names the target.
    """
    index: RuleIndex = {}
    for rule in report["rules"]:
        recipes = [
            (recipe["location"]["start_line"], recipe["text"])
            for recipe in rule["recipes"]
        ]
        is_conditional = bool(rule.get("conditions"))
        for target in rule["targets"]:
            index.setdefault(target, []).append(
                Rule(rule["prerequisites"], recipes, is_conditional)
            )
    return index


def _goals(arguments: list[str]) -> list[str]:
    """Return the goals of a `make` command: its words that are not options."""
    return [word for word in arguments if not word.startswith("-") and "=" not in word]


def _changes_directory(arguments: list[str]) -> bool:
    """Return whether a `make` command's options move it to another directory."""
    return any(word.startswith(("-C", "--directory")) for word in arguments)


def _make_commands(segments: list[list[str]]) -> list[list[str]]:
    """Return the simple commands that call `make`, past any environment prefix."""
    commands = map(command_words, segments)
    return [words for words in commands if words and words[0].lstrip("@-+") == "make"]


def _make_goals(text: str) -> Successors:
    """Return the targets that `make` commands in an expanded recipe line name.

    Only a command in command position counts, so `echo "run make interrogate"`
    leads nowhere. A line the lexer rejects, or a goal left unresolved, clears
    the resolved flag, because the target it would have named is unknown.

    Returns
    -------
    Successors
        The goals named, and whether the line was fully read.
    """
    segments = lex_segments(text)
    if segments is None:
        return Successors([], is_resolved=not re.search(r"\bmake\b", text))
    commands = _make_commands(segments)
    goals = [goal for words in commands for goal in _goals(words[1:])]
    named = [goal for goal in goals if UNRESOLVED_MARK not in goal]
    # `make -C dir target` runs another Makefile, whose targets are not this
    # one's, so the walk cannot follow it and says so.
    delegates = any(_changes_directory(words[1:]) for words in commands)
    return Successors(named, is_resolved=not delegates and len(named) == len(goals))


def _rule_successors(rule: Rule, values: cabc.Mapping[str, str]) -> Successors:
    """Return the targets one rule leads to, by prerequisite or `make` command.

    Returns
    -------
    Successors
        The target names, and whether every one of them was resolved.
    """
    names: list[str] = []
    is_resolved = True
    expansions = [expand(name, values) for name in rule.prerequisites]
    for expansion in expansions:
        names.extend(expansion.text.split())
        is_resolved = is_resolved and expansion.is_resolved
    for _line, text in rule.recipes:
        found = _make_goals(expand(text, values).text)
        names.extend(found.names)
        is_resolved = is_resolved and found.is_resolved
    return Successors(names, is_resolved)


def reachable(
    index: RuleIndex, values: cabc.Mapping[str, str], starts: cabc.Sequence[str]
) -> Successors:
    """Return the targets *starts* reach, and whether the walk resolved fully.

    The walk follows prerequisites and `make name` commands. A prerequisite or
    command that names a variable the expansion cannot settle is skipped and
    clears the resolved flag.

    Returns
    -------
    Successors
        The reachable targets in discovery order, and whether none were skipped.
    """
    seen: list[str] = []
    is_resolved = True
    pending = collections.deque(starts)
    while pending:
        target = pending.popleft()
        if target in seen or target not in index:
            continue
        seen.append(target)
        for rule in index[target]:
            found = _rule_successors(rule, values)
            pending.extend(found.names)
            is_resolved = is_resolved and found.is_resolved
    return Successors(seen, is_resolved)
