"""Read what the gate runs, for the two facts `rust-lint-tables` needs from it.

The lint tables themselves are manifest facts. Two of the rule's checks also
depend on commands the repository's gate runs, which the policy cannot read as
text:

* a boundary crate that permits `unsafe` must have a Miri command the gate
  reaches (LT-004), whether in the Makefile's `lint` or `test` targets (with
  their prerequisites) or in a workflow or composite-action `run` step;
* Whitaker's macro-aware cognitive-complexity lint replaces Clippy's only where
  Whitaker runs over the whole workspace, so the Whitaker commands are carried
  with what each selects.

Both reuse the readers `gate-workspace-coverage` is built on, so the two rules
cannot disagree about which commands the gate has. A command or file the
readers could not place is recorded as unresolved or unreadable, and the policy
reports it instead of guessing.
"""

from __future__ import annotations

import dataclasses
import pathlib
import re
import typing as typ

from .gate_scope_commands import cargo_subcommand_word, without_keywords
from .gate_scope_shell import FRESH_SHELL, runnable
from .gate_scope_sources import GATE_TARGETS, makefile_lines, workflow_lines
from .gate_scope_workflow_model import places, step_runs
from .interrogate_invocation import lex_segments
from .makefile_expansion import expand, single_assignments
from .makefile_reach import reachable, rules_by_target
from .markdown_envelope import load_workflows
from .uv_gate_envelope import load_actions

if typ.TYPE_CHECKING:
    import collections.abc as cabc

    from .gate_scope_commands import Invocation
    from .gate_scope_shell import ShellState
    from .gate_scope_workflow_model import Place
    from .makefile_expansion import Expansion
    from .makefile_facts import MakefileInspector, MakeutilReport
    from .markdown_envelope import WorkflowFile

__all__ = ["GateFacts", "MiriFact", "WhitakerRun", "read_gate"]

_NAMES_MIRI: typ.Final = re.compile(r"(?<![\w-])miri(?![\w-])")
_ASSIGNMENT: typ.Final = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
# Words that run the next command rather than being it.
_LAUNCHERS: typ.Final = frozenset({"env", "time", "command", "exec", "nice"})


def _command_words(segment: list[str]) -> list[str]:
    """Return a simple command's words from its command word on.

    Shell keywords (`then`, `if`), leading `NAME=value` assignments and launcher
    words (`env`, `time`) run the next command; they are not it.

    Returns
    -------
    list[str]
        The command word and its arguments.
    """
    words = without_keywords(segment)
    start = 0
    while start < len(words) and (
        _ASSIGNMENT.match(words[start]) or words[start].lstrip("@-+") in _LAUNCHERS
    ):
        start += 1
    return words[start:]


def _runs_cargo_miri(segment: list[str]) -> bool:
    """Return whether one simple command is `cargo [+toolchain] miri ...`."""
    words = _command_words(segment)
    if not words or pathlib.PurePosixPath(words[0].lstrip("@-+")).name != "cargo":
        return False
    return cargo_subcommand_word(words[1:]) == "miri"


def _line_miri(
    text: str, state: ShellState = FRESH_SHELL
) -> tuple[bool | None, ShellState]:
    """Return whether a line runs `cargo miri`, and the shell the next line starts in.

    The line is lexed into simple commands, so `echo "cargo miri test"` and a
    comment are not runs. Shell control flow is read as `gate-workspace-coverage`
    reads it: a command inside `if false; then ... fi` (also across lines, which
    *state* carries) cannot run and is not counted. A line that will not lex but
    names Miri is unknown, not absent.

    Returns
    -------
    tuple[bool | None, ShellState]
        Whether a command that can run is `cargo miri`, or None when the line
        names Miri but will not lex; and the state after the line.
    """
    segments = lex_segments(text)
    if segments is None:
        return (None if _NAMES_MIRI.search(text) else False), state
    kept, after = runnable(segments, state)
    return any(_runs_cargo_miri(command) for command, _frames in kept), after


class MiriFact(typ.TypedDict):
    """Whether a Miri command is reachable from the gate.

    `is_resolved` is false when something that could hold one could not be read
    (an unresolved Makefile walk, an undecodable workflow, a matrix computed by
    an expression); a false `is_reachable` is then not a proof of absence.
    """

    is_reachable: bool
    is_resolved: bool
    sources: list[str]


class WhitakerRun(typ.TypedDict):
    """One Whitaker command the gate runs, and what it selects."""

    source: str
    is_resolved: bool
    is_workspace: bool
    excludes: list[str]
    packages: list[str]
    changes_directory: bool


class GateFacts(typ.TypedDict):
    """The gate facts of one checkout."""

    miri: MiriFact
    whitaker_runs: list[WhitakerRun]
    unreadable: list[str]


@dataclasses.dataclass(slots=True)
class _MiriScan:
    """Accumulates where Miri was found and whether the scan was complete."""

    sources: list[str] = dataclasses.field(default_factory=list)
    is_resolved: bool = True

    def fact(self) -> MiriFact:
        """Return the accumulated facts as an envelope value."""
        return {
            "is_reachable": bool(self.sources),
            "is_resolved": self.is_resolved,
            "sources": sorted(set(self.sources)),
        }


def _makefile_miri(report: MakeutilReport, scan: _MiriScan) -> None:
    """Record Miri commands in the recipes `lint` and `test` reach."""
    index = rules_by_target(report)
    values = single_assignments(report["variables"])
    walk = reachable(index, values, GATE_TARGETS)
    scan.is_resolved = scan.is_resolved and walk.is_resolved
    # Under `.ONESHELL` a rule's recipe lines are one shell, so an `if false`
    # opened on one line hides the lines after it; otherwise each is its own.
    is_one_shell = ".ONESHELL" in index
    for target in walk.names:
        for rule in index[target]:
            state = FRESH_SHELL
            for number, text in rule.recipes:
                state = _recipe_miri(
                    f"Makefile:{number} ({target})", text, values, scan, state
                )
                if not is_one_shell:
                    state = FRESH_SHELL


def _recipe_miri(
    source: str,
    text: str,
    values: cabc.Mapping[str, str],
    scan: _MiriScan,
    state: ShellState,
) -> ShellState:
    """Record whether one recipe line runs Miri, or might (an unresolved word).

    Returns
    -------
    ShellState
        The shell the next line of the recipe starts in.
    """
    expanded = expand(text, values)
    verdict, after = _line_miri(expanded.text, state)
    if verdict:
        scan.sources.append(source)
    elif verdict is None or _may_hide_miri(expanded, text):
        scan.is_resolved = False
    return after


def _may_hide_miri(expanded: Expansion, text: str) -> bool:
    """Return whether an unresolved Make reference could stand for the command.

    Only a line that names Miri is in doubt: an unresolved word in a line that
    never mentions it cannot be the command.

    Returns
    -------
    bool
        Whether the reference is unresolved and the line names Miri.
    """
    return not expanded.is_resolved and _NAMES_MIRI.search(text) is not None


def _miri_steps(
    path: str, place: Place, combination: dict[str, object], scan: _MiriScan
) -> list[str]:
    """Return the steps of *place*, for one matrix combination, that run Miri."""
    found: list[str] = []
    for step in step_runs(place, combination):
        verdicts = _step_verdicts(step.lines)
        if any(verdicts):
            found.append(f"{path} {step.source}")
        elif None in verdicts:
            scan.is_resolved = False
    return found


def _step_verdicts(lines: list[str]) -> list[bool | None]:
    """Return `_line_miri` for each line of one step, which is one shell.

    The shell state carries from line to line, so an `if false` that opens on
    one line still hides the commands on the lines after it.

    Returns
    -------
    list[bool | None]
        One verdict per line.
    """
    verdicts: list[bool | None] = []
    state = FRESH_SHELL
    for line in lines:
        verdict, state = _line_miri(line, state)
        verdicts.append(verdict)
    return verdicts


def _place_miri(path: str, place: Place, scan: _MiriScan) -> None:
    """Record Miri commands in one job or action, read once per matrix entry."""
    if place.combinations is None:
        # An expression-built matrix cannot be filled in; it only matters when
        # a step might name Miri.
        if _NAMES_MIRI.search(repr(place.steps)):
            scan.is_resolved = False
        return
    for combination in place.combinations:
        scan.sources.extend(_miri_steps(path, place, combination, scan))


def _workflow_miri(files: list[WorkflowFile], scan: _MiriScan) -> None:
    """Record Miri commands in the `run` steps of workflows and actions."""
    for workflow in files:
        if workflow["error"] is not None:
            scan.is_resolved = False
            continue
        for place in places(workflow["parsed"]):
            _place_miri(workflow["path"], place, scan)


def _whitaker_run(invocation: Invocation) -> WhitakerRun:
    """Return the facts of one Whitaker invocation."""
    return {
        "source": invocation["source"],
        "is_resolved": invocation["resolved"],
        "is_workspace": invocation["workspace_flag"],
        "excludes": invocation["excludes"],
        "packages": invocation["packages"],
        "changes_directory": invocation["changes_directory"],
    }


def read_gate(
    checkout: pathlib.Path,
    root: pathlib.Path,
    *,
    inspect: MakefileInspector,
    has_makefile: bool,
) -> GateFacts:
    """Return the Miri and Whitaker facts of the checkout's gate.

    Parameters
    ----------
    checkout:
        Path to the checkout under audit.
    root:
        The checkout's resolved root, for the containment guard.
    inspect:
        Reads the root Makefile's facts.
    has_makefile:
        Whether the checkout has a regular root `Makefile`.

    Returns
    -------
    GateFacts
        Miri reachability, the Whitaker commands, and what could not be read.
    """
    files = [*load_workflows(checkout, root), *load_actions(checkout, root)]
    from_ci = workflow_lines(files)
    scan = _MiriScan()
    unreadable = list(from_ci.unreadable)
    runs = [i for i in from_ci.invocations if i["tool"] == "whitaker"]
    if has_makefile:
        report = inspect(checkout / "Makefile").report
        from_make = makefile_lines(
            report,
            starts=GATE_TARGETS,
            elsewhere={i["tool"] for i in from_ci.invocations},
        )
        _makefile_miri(report, scan)
        unreadable.extend(from_make.unreadable)
        runs.extend(i for i in from_make.invocations if i["tool"] == "whitaker")
    _workflow_miri(files, scan)
    return {
        "miri": scan.fact(),
        "whitaker_runs": [_whitaker_run(i) for i in runs],
        "unreadable": unreadable,
    }
