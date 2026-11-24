"""Classify whether each test entry point fetches dependencies before testing.

A test that starts its own `cargo` resolves dependencies and may update the
crates.io index, and it does so while holding the package-cache lock. On a host
that shares `$CARGO_HOME` between repositories, that turns one repository's
test run into every other repository's wait. The mitigation is to run
`cargo fetch --locked` first, so the lock is taken only to prove the cache is
already warm.

The question this module answers is therefore narrower than "does a fetch
happen somewhere": it is whether a fetch runs *and its failure stops the
tests*. `nested_cargo_scripts` decides that for one script; this module reads
the three places a repository declares a test entry point — the Makefile `test`
target, each nextest profile its commands select, and each workflow job that
runs tests — and joins them, so a `make test` step inherits whatever the
Makefile target proves.

Where the text cannot settle the question — an unresolved Make variable,
`.ONESHELL`, a recursive `make`, an expression-valued workflow command — the
fact says `uncertain` rather than guessing either way.
"""

from __future__ import annotations

import dataclasses
import re
import typing as typ

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from .makefile_expansion import single_assignments
from .makefile_facts import MakefileRefusedError, inspect_makefile
from .makefile_tool_paths import read_tool_paths
from .markdown_envelope import (
    WORKFLOW_SUFFIXES,
    WORKFLOWS_DIRECTORY,
    within_checkout,
)
from .nested_cargo_conditions import (
    condition_proves_order,
    runs_only_after_success,
)
from .nested_cargo_fetch_order import (
    conditional_fetch_precedes,
    gates_fetch,
    line_fetches,
)
from .nested_cargo_makefile_lines import rules_by_target
from .nested_cargo_makefile_scan import TargetScan, scan_target
from .nested_cargo_nextest import (
    ABSENT,
    BEFORE,
    DEFAULT_PROFILE,
    NEXTEST_CONFIG,
    UNCERTAIN,
    NextestConfig,
    read_nextest,
)
from .nested_cargo_release import hides_command, is_planned_dist_job
from .nested_cargo_scripts import (
    TEST_TARGET,
    CommandMeaning,
    ScriptCommand,
    script_commands,
)
from .nested_cargo_shells import ShellRule, effective_shell

if typ.TYPE_CHECKING:
    import pathlib

    from .makefile_facts import MakeRule

OPERATION_READ_WORKFLOW: typ.Final = "read-nested-cargo-workflow"
OPERATION_READ_MAKEFILE: typ.Final = "read-nested-cargo-makefile"
OPERATION_READ_NEXTEST: typ.Final = "read-nested-cargo-nextest"

MAKEFILE: typ.Final = "Makefile"
ONESHELL_TARGET: typ.Final = ".ONESHELL"
# The weakest answer among a probed line's paths is the entry point's answer.
_STATE_RANK: typ.Final = {ABSENT: 0, UNCERTAIN: 1, BEFORE: 2}

_EXPRESSION: typ.Final = re.compile(r"\$\{\{.*?\}\}", re.DOTALL)
_YAML = YAML(typ="rt")


class EntryPointFact(typ.TypedDict):
    """One place where a repository runs its tests.

    ``state`` is the answer the policy acts on; ``reason`` is the short
    sentence a reviewer needs to see why.
    """

    kind: str
    path: str
    line: int
    name: str
    state: str
    reason: str


def _first_recipe_line(index: dict[str, list[MakeRule]]) -> int:
    """Return the first recipe line of `test`, or the rule's own line.

    A recipe that could not be read has no test command to point at, so the
    first line of the recipe is where a reviewer would start.

    Returns
    -------
    int
        The 1-based line number.
    """
    for rule in index[TEST_TARGET]:
        recipes = rule["recipes"]
        if recipes:
            return recipes[0]["location"]["start_line"]
        return rule["location"]["start_line"]
    return 1


@dataclasses.dataclass(frozen=True, slots=True)
class _MakefileEntry:
    """The Makefile's `test` entry point, and the nextest profiles it selects.

    ``refused`` records a Makefile the loader could not parse. It contributes
    no fact of its own — the reader never saw the target it might contain —
    but a workflow step that delegates to `make test` inherits the refusal
    rather than a clean `absent`.
    """

    fact: EntryPointFact | None
    profiles: list[str]
    refused: bool = False


def _makefile_entry(
    checkout: pathlib.Path,
    root: pathlib.Path,
    problems: list[str],
    config: NextestConfig,
) -> _MakefileEntry:
    """Return the Makefile's `test` entry point, if it has a test command.

    The Makefile is a policy input, so it is read only once containment is
    proved. `Path.is_file()` follows symbolic links, which would otherwise let
    a checkout aim its `Makefile` at a readable file elsewhere on the machine
    and report that file's test target as this repository's entry point.

    Returns
    -------
    _MakefileEntry
        The entry point fact, the profiles it selects, and whether the file was
        there but could not be parsed.
    """
    path = checkout / MAKEFILE
    if not path.is_file():
        return _MakefileEntry(None, [])
    within_checkout(root, path, OPERATION_READ_MAKEFILE)
    try:
        facts = inspect_makefile(path)
    except MakefileRefusedError as error:
        problems.append(f"cannot parse {MAKEFILE}: {error}")
        return _MakefileEntry(None, [], refused=True)
    report = facts.report
    values = read_tool_paths(single_assignments(report["variables"]))
    index = rules_by_target(report)
    if TEST_TARGET not in index:
        return _MakefileEntry(None, [])
    scans = scan_target(index, values, sequencing_holds=ONESHELL_TARGET not in index)
    profiles: list[str] = []
    weakest = _weakest_path(scans, config, profiles, _first_recipe_line(index))
    if weakest is None:
        return _MakefileEntry(None, [])
    state, reason, line = weakest
    fact = EntryPointFact(
        kind="makefile",
        path=MAKEFILE,
        line=line,
        name=TEST_TARGET,
        state=state,
        reason=reason,
    )
    return _MakefileEntry(fact, profiles)


def _weakest_path(
    scans: list[TargetScan],
    config: NextestConfig,
    profiles: list[str],
    default_line: int,
) -> tuple[str, str, int] | None:
    """Return the state, reason and line of the weakest path through `test`.

    A probed line has a path per runner and the order has to hold on each. A
    path that never reaches a test command may still hide one behind another
    target; the target then exists but its test could not be read, and
    dropping it would let a workflow step that delegates to it inherit
    `absent`, which claims a cold cache the reader cannot see.

    Returns
    -------
    tuple[str, str, int] | None
        The weakest answer, or None when no path has a test or hides one.
    """
    found = [scan for scan in scans if scan.is_found]
    candidates = [
        (*_makefile_state(scan, config, profiles), scan.line) for scan in found
    ]
    hidden = next(
        (s.hidden_reason for s in scans if not s.is_found and s.hidden_reason), ""
    )
    line = found[0].line if found else default_line
    if hidden:
        candidates.append((UNCERTAIN, hidden, line))
    if any(scan.is_truncated for scan in scans):
        reason = "the recipe picks its runner on too many lines to follow"
        candidates.append((UNCERTAIN, reason, line))
    if not candidates:
        return None
    return min(candidates, key=lambda candidate: _STATE_RANK[candidate[0]])


def _makefile_state(
    scan: TargetScan, config: NextestConfig, profiles: list[str]
) -> tuple[str, str]:
    """Return the state a `test` scan proves, with the reason a reviewer needs."""
    if scan.is_cold_hidden:
        # What a delegated or unreadable command ran before any fetch is not
        # covered by a fetch or setup script that comes after it.
        return UNCERTAIN, scan.hidden_reason
    if scan.test == "nextest" and not scan.proven:
        # A profile is recorded only where this entry point relies on it: one
        # that fetches first covers its own run, and owes the profile nothing.
        profiles.append(scan.profile or DEFAULT_PROFILE)
        inherited = config.state(scan.profile)
        if inherited == BEFORE:
            return BEFORE, "the nextest profile's setup script fetches first"
        if inherited == UNCERTAIN:
            return UNCERTAIN, "the nextest configuration could not be read"
    if scan.proven:
        return BEFORE, scan.proven_reason
    if scan.uncertain_reason:
        return UNCERTAIN, scan.uncertain_reason
    return ABSENT, "no success-gated fetch precedes the test command"


def _visible(run: str) -> tuple[str, bool]:
    """Return a workflow command with its expressions removed, and whether any were."""
    return _EXPRESSION.sub("", run), bool(_EXPRESSION.search(run))


def _mapping(value: object) -> dict[str, object] | None:
    """Return *value* as a string-keyed mapping, or ``None`` when it is not one.

    The YAML decoder returns its own mapping type, so the declaration is a
    narrowing cast over a runtime check rather than a conversion.

    Returns
    -------
    dict[str, object] | None
        *value* when it is a mapping, otherwise ``None``.
    """
    return typ.cast("dict[str, object]", value) if isinstance(value, dict) else None


def _allows_failure(step: dict[str, object]) -> bool:
    """Return whether a step may fail without stopping the job."""
    value = step.get("continue-on-error")
    return value is not None and value is not False


def _gated(*, prefetch_proved: bool, step: dict[str, object]) -> bool | None:
    """Return whether an earlier step's fetch gates *step*.

    Two facts have to hold: an earlier step ran a fetch whose failure would stop
    the job, and this step runs only after success. The first is accumulated by
    the caller across the job's steps. The second is the step's own `if:`, and
    it is three-valued — an unreadable condition is neither gated nor ungated.

    Returns
    -------
    bool | None
        True, False, or None when the condition cannot be decided.
    """
    if not prefetch_proved:
        return False
    return runs_only_after_success(step)


def _node_line(node: object, fallback: int) -> int:
    """Return the 1-based source line of a YAML node, or *fallback*."""
    location = getattr(node, "lc", None)
    line = getattr(location, "line", None)
    return line + 1 if isinstance(line, int) else fallback


def _inherited_state(
    meaning: CommandMeaning,
    make_entry: _MakefileEntry,
    config: NextestConfig,
    profiles: list[str],
) -> str:
    """Return what a delegated `make test` or `cargo nextest run` already proves."""
    if meaning.test == "make":
        if make_entry.fact is None:
            return UNCERTAIN if make_entry.refused else ABSENT
        return make_entry.fact["state"]
    if meaning.test == "nextest":
        profiles.append(meaning.profile or DEFAULT_PROFILE)
        return config.state(meaning.profile)
    return ABSENT


def _prefetch_precedes(commands: list[ScriptCommand], position: int) -> bool:
    """Report whether a fetch appears before *position*, however it is chained.

    Returns
    -------
    bool
        True when an earlier command of this step is a locked fetch.
    """
    return any(command.meaning.is_prefetch for command in commands[:position])


def _step_state(
    command: ScriptCommand,
    position: int,
    commands: list[ScriptCommand],
    *,
    has_expression: bool,
    gated: bool | None,
    shell: ShellRule,
    make_entry: _MakefileEntry,
    config: NextestConfig,
    profiles: list[str],
) -> tuple[str, str]:
    """Return the prefetch state and reason for one workflow test command.

    *gated* is three-valued. True means an earlier step's fetch is proved to
    have succeeded before this step could run; False means it is not; None
    means an earlier fetch ran but this step's `if:` decides on operands the
    reader cannot evaluate, so neither answer is honest.

    Returns
    -------
    tuple[str, str]
        The state — `before`, `uncertain` or `absent` — and a reason a reader
        can check against the workflow.
    """
    # A fetch that gates this command covers its own run, so the command does
    # not rely on the nextest profile and records none; asking for the
    # inherited state first would record the profile for a run that never
    # needed it.
    if gated:
        return BEFORE, "an earlier step runs cargo fetch --locked"
    if gates_fetch(commands, position) and not has_expression:
        # The chain proves the order only when the command text is settled: an
        # expression may supply the `|| true` that hides a failed fetch.
        return BEFORE, "cargo fetch --locked runs first in the same step"
    inherited = _inherited_state(command.meaning, make_entry, config, profiles)
    if inherited == BEFORE:
        return BEFORE, "the delegated test target fetches first"
    if gated is None:
        return UNCERTAIN, "the step's if condition cannot be shown to require success"
    if has_expression:
        return UNCERTAIN, "part of the step's command is an expression"
    if inherited == UNCERTAIN:
        return UNCERTAIN, "the delegated test target could not be read"
    if conditional_fetch_precedes(commands, position):
        # A fetch inside a branch may not run at all, so neither its success
        # nor the paths that skipped it can be settled either way.
        return UNCERTAIN, "cargo fetch --locked runs only on some paths"
    if not shell.is_known and _prefetch_precedes(commands, position):
        # The only thing standing between this fetch and the test is a line
        # break, and whether that gates depends on a shell nobody named.
        return UNCERTAIN, "the step's shell cannot be shown to stop at a failed fetch"
    return ABSENT, "no success-gated fetch precedes the test"


@dataclasses.dataclass(frozen=True, slots=True)
class _StepScan:
    """The test entry point of one workflow step and whether it fetches first."""

    facts: list[EntryPointFact]
    fetches: bool


def _step_scan(
    relative: str,
    job: str,
    step: dict[str, object],
    run: str,
    *,
    gated: bool | None,
    shell: ShellRule,
    make_entry: _MakefileEntry,
    config: NextestConfig,
    profiles: list[str],
    releases: bool = False,
) -> _StepScan:
    """Classify one `run` step, returning its facts and whether it fetches first.

    *releases* says the step's job builds release artefacts with cargo-dist,
    so a step that is only a matrix lookup installs the tool and is no test.

    Returns
    -------
    _StepScan
        The step's entry-point facts and whether it fetches first.
    """
    visible, has_expression = _visible(run)
    commands = script_commands(visible, newline_gates=shell.fail_fast)
    fetches = line_fetches(commands, is_resolved=True) and not has_expression
    line = _node_line(step, 1)
    for position, command in enumerate(commands):
        if command.meaning.test is None:
            continue
        state, reason = _step_state(
            command,
            position,
            commands,
            has_expression=has_expression,
            gated=gated,
            shell=shell,
            make_entry=make_entry,
            config=config,
            profiles=profiles,
        )
        fact = EntryPointFact(
            kind="workflow",
            path=relative,
            line=line,
            name=job,
            state=state,
            reason=reason,
        )
        return _StepScan(facts=[fact], fetches=fetches)
    if hides_command(visible, run, has_expression=has_expression, releases=releases):
        fact = EntryPointFact(
            kind="workflow",
            path=relative,
            line=line,
            name=job,
            state=UNCERTAIN,
            reason="the step's command is an expression",
        )
        return _StepScan(facts=[fact], fetches=False)
    return _StepScan(facts=[], fetches=fetches)


def _job_entries(
    relative: str,
    parsed: dict[str, object],
    make_entry: _MakefileEntry,
    config: NextestConfig,
    profiles: list[str],
) -> list[EntryPointFact]:
    """Return the entry points of every job in one decoded workflow."""
    jobs = _mapping(parsed.get("jobs"))
    if jobs is None:
        return []
    # `_default_shell` reads `defaults.run.shell` out of the mapping it is
    # given, so the workflow itself is passed: its top-level `defaults` block
    # would be one level too shallow and its shell silently dropped.
    workflow = parsed
    facts: list[EntryPointFact] = []
    for job_name, raw_job in jobs.items():
        job = _mapping(raw_job)
        if job is None:
            continue
        steps = job.get("steps")
        if not isinstance(steps, list):
            continue
        runs_on = job.get("runs-on")
        releases = is_planned_dist_job(job, jobs)
        fetches = False
        for raw_step in steps:
            step = _mapping(raw_step)
            if step is None:
                continue
            run = step.get("run")
            if not isinstance(run, str):
                continue
            scan = _step_scan(
                relative,
                str(job_name),
                step,
                run,
                gated=_gated(prefetch_proved=fetches, step=step),
                shell=effective_shell(step, job, workflow, runs_on),
                make_entry=make_entry,
                config=config,
                profiles=profiles,
                releases=releases,
            )
            facts.extend(scan.facts)
            if (
                scan.fetches
                and not _allows_failure(step)
                and condition_proves_order(step)
            ):
                fetches = True
    return facts


def _read_workflow(
    checkout: pathlib.Path,
    root: pathlib.Path,
    relative: str,
    problems: list[str],
) -> dict[str, object] | None:
    """Decode one workflow file, recording a problem rather than dropping it."""
    path = checkout / relative
    within_checkout(root, path, OPERATION_READ_WORKFLOW)
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        problems.append(f"cannot read {relative}: {error}")
        return None
    try:
        parsed: object = _YAML.load(text)
    except YAMLError as error:
        problems.append(f"cannot decode {relative}: {error}")
        return None
    document = _mapping(parsed)
    if document is None:
        problems.append(f"{relative} is not a workflow mapping")
    return document


def _workflow_entries(
    checkout: pathlib.Path,
    root: pathlib.Path,
    problems: list[str],
    make_entry: _MakefileEntry,
    config: NextestConfig,
    profiles: list[str],
) -> list[EntryPointFact]:
    """Return one fact per test entry point across every workflow file."""
    directory = checkout / WORKFLOWS_DIRECTORY
    if not directory.is_dir():
        return []
    try:
        entries = sorted(directory.iterdir(), key=lambda entry: entry.name)
    except OSError as error:
        problems.append(f"cannot list {WORKFLOWS_DIRECTORY}: {error}")
        return []
    facts: list[EntryPointFact] = []
    for entry in entries:
        if entry.suffix not in WORKFLOW_SUFFIXES or not entry.is_file():
            continue
        relative = (WORKFLOWS_DIRECTORY / entry.name).as_posix()
        parsed = _read_workflow(checkout, root, relative, problems)
        if parsed is not None:
            facts.extend(_job_entries(relative, parsed, make_entry, config, profiles))
    return facts


def _reason_for(state: str) -> str:
    """Return the sentence a reviewer needs for one profile's state.

    Returns
    -------
    str
        Why the selected profile does or does not prove the cache warm.
    """
    if state == UNCERTAIN:
        return "the nextest setup scripts could not be read or resolved"
    if state == BEFORE:
        return "an all() setup script runs cargo fetch --locked"
    return "no all() setup script runs cargo fetch --locked"


def _nextest_facts(config: NextestConfig, profiles: list[str]) -> list[EntryPointFact]:
    """Return one fact per nextest profile a test command selected.

    A repository with no `.config/nextest.toml` has no setup scripts to
    configure, so it gets no configuration fact: the entry point there is the
    test command, which the caller already reports.

    Returns
    -------
    list[EntryPointFact]
        One fact per selected profile, or none when there is no configuration
        file to point at.
    """
    if not config.is_present:
        return []
    facts: list[EntryPointFact] = []
    for profile in profiles:
        state = config.state(profile)
        found = config.profile(profile)
        reason = _reason_for(state)
        facts.append(
            EntryPointFact(
                kind="nextest",
                path=NEXTEST_CONFIG,
                line=found.line,
                name=profile,
                state=state,
                reason=reason,
            )
        )
    return facts


def collect_entry_points(
    checkout: pathlib.Path, problems: list[str]
) -> list[EntryPointFact]:
    """Return one fact per test entry point in *checkout*.

    The Makefile is read first, then the nextest profiles its commands select,
    then the workflows. Problems are appended to *problems* rather than
    dropped: a file the reader could not examine is not a file that is absent.

    Parameters
    ----------
    checkout:
        The repository root to read.
    problems:
        The fact list; every unreadable or undecodable input appends one
        sentence.

    Returns
    -------
    list[EntryPointFact]
        The entry points, ordered Makefile, nextest, then workflow.
    """
    try:
        root = checkout.resolve(strict=True)
    except OSError:
        root = checkout
    # The nextest configuration is a policy input too: its setup scripts decide
    # whether a `cargo nextest run` entry point is `before`. Reading it through
    # the guard keeps a checkout from pointing it at an outside file whose
    # scripts would then answer for this repository. An absent file returns
    # False here and `read_nextest` reports it as not present, as before.
    within_checkout(root, checkout / NEXTEST_CONFIG, OPERATION_READ_NEXTEST)
    config = read_nextest(checkout, problems)
    entry = _makefile_entry(checkout, root, problems, config)
    profiles: list[str] = list(entry.profiles)
    workflows = _workflow_entries(checkout, root, problems, entry, config, profiles)
    facts = [] if entry.fact is None else [entry.fact]
    selected = list(dict.fromkeys(profiles))
    return facts + _nextest_facts(config, selected) + workflows
