"""Read the root Makefile's rules for the `rust-prover-tooling` rule.

A workflow step that runs `make X` installs a prover when `X`, or any
prerequisite of `X`, runs an installer. The pinned `makeutil` report supplies
the rules; this module flattens it into prerequisites and recipe commands and
follows them. A bare `make` builds `.DEFAULT_GOAL`, or the first ordinary
target.
"""

from __future__ import annotations

import dataclasses
import typing as typ

from .makefile_facts import MakefileRefusedError
from .markdown_envelope import is_file, within_checkout
from .shell_commands import Argv, commands

if typ.TYPE_CHECKING:
    import pathlib

    from .makefile_facts import MakefileInspector, MakeutilReport
    from .prover_tooling_types import MakefileState

OPERATION: typ.Final = "read-prover-tooling-facts"
DEFAULT_GOAL_VARIABLE: typ.Final = ".DEFAULT_GOAL"


@dataclasses.dataclass(frozen=True, slots=True)
class MakeRules:
    """The Makefile's rules, flattened: prerequisites and recipe commands."""

    prerequisites: dict[str, list[str]]
    recipes: dict[str, list[Argv]]
    default_goal: str | None = None


def closure(rules: MakeRules, targets: list[str]) -> list[str]:
    """Return *targets* and every prerequisite they reach.

    Returns
    -------
    list[str]
        Each reachable target that has a rule, once.
    """
    seen: list[str] = []
    todo = list(targets)
    while todo:
        target = todo.pop()
        if target in seen:
            continue
        if target in rules.prerequisites:
            seen.append(target)
            todo.extend(rules.prerequisites[target])
    return seen


def commands_reached(rules: MakeRules, targets: list[str]) -> list[Argv]:
    """Return every recipe command of *targets* and their prerequisites."""
    return [
        argv for target in closure(rules, targets) for argv in rules.recipes[target]
    ]


def all_commands(rules: MakeRules) -> list[Argv]:
    """Return every recipe command in the Makefile."""
    return [argv for argvs in rules.recipes.values() for argv in argvs]


def goals(rules: MakeRules, argv: Argv) -> list[str]:
    """Return the targets a `make` command builds: those named, else the default."""
    named = [w for w in argv[1:] if not w.startswith("-") and "=" not in w]
    if named:
        return named
    return [rules.default_goal] if rules.default_goal else []


EMPTY_RULES: typ.Final = MakeRules({}, {})


def _is_ordinary(target: str) -> bool:
    """Return whether *target* can be a default goal."""
    return not target.startswith(".") and "%" not in target


def _declared_goal(report: MakeutilReport) -> str | None:
    """Return the goal `.DEFAULT_GOAL` names, if the Makefile sets it."""
    for variable in typ.cast("list[dict[str, object]]", report["variables"]):
        if variable.get("name") == DEFAULT_GOAL_VARIABLE:
            return str(variable.get("raw_value", "")).strip() or None
    return None


def _first_goal(report: MakeutilReport) -> str | None:
    """Return the first ordinary target in the Makefile."""
    targets = (t for rule in report["rules"] for t in rule["targets"])
    return next((t for t in targets if _is_ordinary(t)), None)


def make_rules(report: MakeutilReport) -> MakeRules:
    """Flatten a `makeutil` report into prerequisites and recipe commands.

    Returns
    -------
    MakeRules
        The rules, with the goal a bare `make` builds.
    """
    prerequisites: dict[str, list[str]] = {}
    recipes: dict[str, list[Argv]] = {}
    for rule in report["rules"]:
        parsed = [
            argv for recipe in rule["recipes"] for argv in commands(recipe["text"])
        ]
        for target in rule["targets"]:
            prerequisites.setdefault(target, []).extend(rule["prerequisites"])
            recipes.setdefault(target, []).extend(parsed)
    goal = _declared_goal(report) or _first_goal(report)
    return MakeRules(prerequisites, recipes, goal)


def _has_makefile(checkout: pathlib.Path, root: pathlib.Path) -> bool:
    """Return whether the root Makefile exists as a regular file in the checkout."""
    path = checkout / "Makefile"
    if not within_checkout(root, path, OPERATION):
        return False
    return is_file(path, OPERATION)


def read_makefile(
    checkout: pathlib.Path, root: pathlib.Path, inspect: MakefileInspector
) -> tuple[MakefileState, MakeutilReport | None]:
    """Return the Makefile's state and report; a refusal is a fact, not a raise.

    Returns
    -------
    tuple[MakefileState, MakeutilReport | None]
        ``absent``, ``ok`` with the report, or ``refused`` with the reason.
    """
    if not _has_makefile(checkout, root):
        return {"state": "absent", "error": None}, None
    try:
        return {"state": "ok", "error": None}, inspect(checkout / "Makefile").report
    except MakefileRefusedError as error:
        return {"state": "refused", "error": str(error)}, None
