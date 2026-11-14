"""Read the root Makefile's rules for the `rust-prover-tooling` rule.

A workflow step that runs `make X` installs a prover when `X`, or any
prerequisite of `X`, runs an installer. The pinned `makeutil` report supplies
the rules; this module flattens it into prerequisites and recipe commands and
follows them. A bare `make` builds `.DEFAULT_GOAL`, or the first ordinary
target; the operands of options such as `-j 2` are not goals.
"""

from __future__ import annotations

import dataclasses
import re
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
# Make options whose next word is their operand, not a target.
OPTIONS_WITH_ARGUMENT: typ.Final = frozenset({
    "-f",
    "-C",
    "-I",
    "-o",
    "-W",
    "--file",
    "--makefile",
    "--directory",
    "--include-dir",
    "--old-file",
    "--what-if",
    "--new-file",
    "--assume-old",
    "--assume-new",
})
# Options whose operand is optional: it is theirs only when it is a number.
JOB_OPTIONS: typ.Final = frozenset({
    "-j",
    "--jobs",
    "-l",
    "--load-average",
    "--max-load",
})


@dataclasses.dataclass(frozen=True, slots=True)
class MakeRules:
    """The Makefile's rules, flattened: prerequisites and recipe commands."""

    prerequisites: dict[str, list[str]]
    recipes: dict[str, list[Argv]]
    default_goal: str | None = None


EMPTY_RULES: typ.Final = MakeRules({}, {})


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


def _consumes_next(option: str, following: str | None) -> bool:
    """Return whether *option* takes *following* as its operand."""
    if option in OPTIONS_WITH_ARGUMENT:
        return following is not None
    return option in JOB_OPTIONS and following is not None and following.isdigit()


def _operands(words: list[str]) -> list[str]:
    """Return the words of a `make` command that are targets."""
    found: list[str] = []
    skip = False
    for index, word in enumerate(words):
        following = words[index + 1] if index + 1 < len(words) else None
        if skip:
            skip = False
        elif word.startswith("-"):
            skip = _consumes_next(word, following)
        elif "=" not in word:
            found.append(word)
    return found


def goals(rules: MakeRules, argv: Argv) -> list[str]:
    """Return the targets a `make` command builds: those named, else the default.

    Returns
    -------
    list[str]
        The named targets, or the default goal when none is named.
    """
    named = _operands(argv[1:])
    if named:
        return named
    return [rules.default_goal] if rules.default_goal else []


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


REFERENCE: typ.Final = re.compile(r"(?<!\$)\$[({](\w+)[)}]")
EXPANSION_DEPTH: typ.Final = 4


def _variable_table(report: MakeutilReport) -> dict[str, str]:
    """Return each variable the Makefile assigns, by name, as written."""
    variables = typ.cast("list[dict[str, object]]", report["variables"])
    return {
        str(v["name"]): str(v.get("raw_value", "")).strip()
        for v in variables
        if v.get("name") and v.get("name") != DEFAULT_GOAL_VARIABLE
    }


def _expand(text: str, table: dict[str, str]) -> str:
    """Replace `$(NAME)` and `${NAME}` with the variable's written value.

    Only a variable the Makefile itself assigns is replaced, a few levels deep;
    a built-in (`$(CURDIR)`), a function (`$(shell ...)`) or an automatic
    variable stays as written and is masked by the shell splitter.

    Returns
    -------
    str
        The text with the Makefile's own variables replaced.
    """
    for _ in range(EXPANSION_DEPTH):
        replaced = REFERENCE.sub(lambda m: table.get(m.group(1), m.group(0)), text)
        if replaced == text:
            break
        text = replaced
    return text


def make_rules(report: MakeutilReport, *, expand: bool = False) -> MakeRules:
    """Flatten a `makeutil` report into prerequisites and recipe commands.

    Parameters
    ----------
    report:
        The `makeutil` report for the root Makefile.
    expand:
        Whether to replace the Makefile's own variables in each recipe before it
        is split, so a recipe that runs `$(VERUS_RUN) --proof-file x.rs` shows
        the command the variable stands for. PT-006 needs this to see that a
        target runs proofs; PT-002 reads the recipes as written.

    Returns
    -------
    MakeRules
        The rules, with the goal a bare `make` builds.
    """
    table = _variable_table(report) if expand else {}
    prerequisites: dict[str, list[str]] = {}
    recipes: dict[str, list[Argv]] = {}
    for rule in report["rules"]:
        parsed = [
            argv
            for recipe in rule["recipes"]
            for argv in commands(_expand(recipe["text"], table))
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
