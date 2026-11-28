"""Read which Makefile goals run before the tests.

`make:<goal>` names a goal that warms the tests. It counts when the Makefile's
`test` target depends on it (Make runs prerequisites first), or when a recipe
line of `test`, or of a goal it runs, calls `make <goal>` *before* tests start:

- a call counts only if it always runs: first on its line, or joined to the
  calls before it by `&&`, and not after `||`, in a quoted string, inside a
  subshell or in a shell branch;
- a call after a line that runs a test command, or after a goal that itself
  starts tests, is too late and does not count;
- a prerequisite written with a variable, or an included Makefile, may name the
  goal, so a goal not found then is unreadable, not unresolved.
"""

from __future__ import annotations

import dataclasses
import typing as typ

from .nested_cargo_make_order import Index, has_hidden_prerequisite, next_goals
from .nested_cargo_makefile_lines import rules_by_target
from .nested_cargo_makefile_preparation import includes_a_file, read_makefile
from .nested_cargo_scripts import TEST_TARGET

if typ.TYPE_CHECKING:
    import pathlib


@dataclasses.dataclass(frozen=True, slots=True)
class MakeGoals:
    """Hold the goals that run before the tests, and whether more may hide.

    Attributes
    ----------
    goals:
        Every goal `test` depends on, or calls before tests start.
    is_opaque:
        True when a variable prerequisite or an included file may name more.
    is_absent:
        True when the checkout has no Makefile at all.
    """

    goals: frozenset[str]
    is_opaque: bool
    is_absent: bool


def _reachable_goals(index: Index) -> tuple[set[str], bool]:
    """Walk from `test` through prerequisites and early `make` calls.

    Returns
    -------
    tuple[set[str], bool]
        Every goal reached, and whether a prerequisite hid behind a variable.
    """
    goals: set[str] = set()
    hides = False
    pending, walked = [TEST_TARGET], set[str]()
    while pending:
        target = pending.pop()
        if target in walked:
            continue
        walked.add(target)
        names = next_goals(target, index)
        hides = hides or has_hidden_prerequisite(target, index)
        goals.update(names)
        pending.extend(names)
    return goals - {TEST_TARGET}, hides


def read_make_goals(checkout: pathlib.Path) -> MakeGoals:
    """Return the goals that run before or inside the Makefile's `test` target.

    Returns
    -------
    MakeGoals
        The goals, whether the Makefile may hide more, and whether it is absent.
    """
    read = read_makefile(checkout)
    if read is None:
        absent = not (checkout / "Makefile").exists()
        return MakeGoals(frozenset(), is_opaque=True, is_absent=absent)
    report, text = read
    goals, hides = _reachable_goals(rules_by_target(report))
    is_opaque = hides or includes_a_file(report, text)
    return MakeGoals(frozenset(goals), is_opaque, is_absent=False)
