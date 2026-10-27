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

from .makefile_expansion import (
    Expansion,
    expand,
    single_assignments,
)
from .makefile_facts import MakefileRefusedError, inspect_makefile
from .markdown_envelope import (
    WORKFLOW_SUFFIXES,
    WORKFLOWS_DIRECTORY,
    within_checkout,
)
from .nested_cargo_nextest import (
    ABSENT,
    BEFORE,
    DEFAULT_PROFILE,
    NEXTEST_CONFIG,
    UNCERTAIN,
    NextestConfig,
    read_nextest,
)
from .nested_cargo_scripts import (
    TEST_TARGET,
    CommandMeaning,
    ScriptCommand,
    gates_fetch,
    line_fetches,
    script_commands,
)
from .nested_cargo_shells import ShellRule, effective_shell

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    import pathlib

    from .makefile_facts import MakeRecipe, MakeRule, MakeutilReport

OPERATION_READ_WORKFLOW: typ.Final = "read-nested-cargo-workflow"

MAKEFILE: typ.Final = "Makefile"
ONESHELL_TARGET: typ.Final = ".ONESHELL"

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


@dataclasses.dataclass(slots=True)
class _TargetScan:
    """What the `test` target says, accumulated line by line.

    ``uncertain_reason`` explains why a found test command's ordering is not
    proven. ``hidden_reason`` explains why the target may hold a test command
    the reader could not see at all, which is a different claim: a target that
    only builds is not an entry point, but a target whose command is unknown
    must not be reported as one that runs no tests.
    """

    is_found: bool = False
    test: str | None = None
    profile: str | None = None
    line: int = 1
    proven: bool = False
    proven_reason: str = ""
    uncertain_reason: str = ""
    hidden_reason: str = ""


def _rules_by_target(report: MakeutilReport) -> dict[str, list[MakeRule]]:
    """Index the Makefile's rules by target, in file order."""
    index: dict[str, list[MakeRule]] = {}
    for rule in report["rules"]:
        for target in rule["targets"]:
            index.setdefault(target, []).append(rule)
    return index


def _prefetch_in_prerequisites(
    index: dict[str, list[MakeRule]], values: cabc.Mapping[str, str]
) -> tuple[bool, str, str]:
    """Walk `test`'s prerequisites, returning proof, uncertainty and a reason."""
    seen = {TEST_TARGET}
    pending: list[MakeRule] = list(index[TEST_TARGET])
    proven = False
    proven_reason = ""
    uncertain_reason = ""
    while pending:
        rule = pending.pop()
        for reference in rule["prerequisites"]:
            expansion = expand(reference, values)
            if not expansion.is_resolved:
                uncertain_reason = "a prerequisite names an unresolved variable"
                continue
            for name in expansion.text.split():
                if name in seen:
                    continue
                seen.add(name)
                for nested in index.get(name, []):
                    pending.append(nested)
                    if _recipes_fetch(nested["recipes"], values):
                        proven = True
                        proven_reason = f"prerequisite {name} runs cargo fetch --locked"
    return proven, uncertain_reason, proven_reason


def _recipes_fetch(recipes: list[MakeRecipe], values: cabc.Mapping[str, str]) -> bool:
    """Return whether a recipe line fetches with its failure left fatal."""
    for recipe in recipes:
        expansion: Expansion = expand(recipe["text"], values)
        commands = script_commands(expansion.text)
        if not recipe["ignore_errors"] and line_fetches(
            commands, is_resolved=expansion.is_resolved
        ):
            return True
    return False


def _scan_target(
    index: dict[str, list[MakeRule]],
    values: cabc.Mapping[str, str],
    *,
    sequencing_holds: bool,
) -> _TargetScan:
    """Read every rule that defines `test`, in file order, stopping at its test."""
    scan = _TargetScan()
    if not sequencing_holds:
        scan.uncertain_reason = "the Makefile declares .ONESHELL"
    else:
        proven, uncertain_reason, proven_reason = _prefetch_in_prerequisites(
            index, values
        )
        scan.proven = proven
        scan.proven_reason = proven_reason
        scan.uncertain_reason = uncertain_reason
    for rule in index[TEST_TARGET]:
        for recipe in rule["recipes"]:
            if _inspect_line(scan, recipe, values, sequencing_holds=sequencing_holds):
                return scan
    return scan


def _inspect_line(
    scan: _TargetScan,
    recipe: MakeRecipe,
    values: cabc.Mapping[str, str],
    *,
    sequencing_holds: bool,
) -> bool:
    """Read one recipe line, returning whether the target's test was found.

    The scan stops at the first test command. A fetch on a later line runs
    after the test, so it proves nothing about it, whatever `gates_next` says.

    Returns
    -------
    bool
        True when this line holds the target's test command.
    """
    expansion: Expansion = expand(recipe["text"], values)
    commands = script_commands(expansion.text)
    if not expansion.is_resolved:
        scan.uncertain_reason = "a recipe line names an unresolved variable"
    for position, command in enumerate(commands):
        if command.meaning.test is not None:
            _record_test(
                scan,
                command,
                recipe,
                commands,
                expansion,
                position,
                sequencing_holds=sequencing_holds,
            )
            return True
        if command.meaning.is_recursive_make:
            scan.uncertain_reason = "the recipe delegates to another make target"
            scan.hidden_reason = scan.hidden_reason or scan.uncertain_reason
        elif command.meaning.is_unreadable:
            scan.uncertain_reason = "a recipe command could not be read"
            scan.hidden_reason = scan.hidden_reason or scan.uncertain_reason
    if (
        sequencing_holds
        and not recipe["ignore_errors"]
        and line_fetches(commands, is_resolved=expansion.is_resolved)
    ):
        scan.proven = True
        scan.proven_reason = "an earlier recipe line runs cargo fetch --locked"
    return False


def _record_test(
    scan: _TargetScan,
    command: ScriptCommand,
    recipe: MakeRecipe,
    commands: list[ScriptCommand],
    expansion: Expansion,
    position: int,
    *,
    sequencing_holds: bool,
) -> None:
    """Record the first test command of the target, and what precedes it."""
    scan.is_found = True
    scan.test = command.meaning.test
    scan.profile = command.meaning.profile
    scan.line = recipe["location"]["start_line"]
    if scan.proven:
        return
    if (
        sequencing_holds
        and not recipe["ignore_errors"]
        and expansion.is_resolved
        and gates_fetch(commands, position)
    ):
        scan.proven = True
        scan.proven_reason = "cargo fetch --locked runs first on the same line"
    elif not expansion.is_resolved:
        scan.uncertain_reason = "the test command names an unresolved variable"


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
    checkout: pathlib.Path, problems: list[str], config: NextestConfig
) -> _MakefileEntry:
    """Return the Makefile's `test` entry point, if it has a test command."""
    path = checkout / MAKEFILE
    if not path.is_file():
        return _MakefileEntry(None, [])
    try:
        facts = inspect_makefile(path)
    except MakefileRefusedError as error:
        problems.append(f"cannot parse {MAKEFILE}: {error}")
        return _MakefileEntry(None, [], refused=True)
    report = facts.report
    values = single_assignments(report["variables"])
    index = _rules_by_target(report)
    if TEST_TARGET not in index:
        return _MakefileEntry(None, [])
    scan = _scan_target(index, values, sequencing_holds=ONESHELL_TARGET not in index)
    if not scan.is_found:
        if not scan.hidden_reason:
            return _MakefileEntry(None, [])
        # The target exists but its test command could not be read. Dropping
        # the entry point would let a workflow step that delegates to it
        # inherit `absent`, which claims a cold cache the reader cannot see.
        fact = EntryPointFact(
            kind="makefile",
            path=MAKEFILE,
            line=_first_recipe_line(index),
            name=TEST_TARGET,
            state=UNCERTAIN,
            reason=scan.hidden_reason,
        )
        return _MakefileEntry(fact, [])
    profiles: list[str] = []
    state, reason = _makefile_state(scan, config, profiles)
    fact = EntryPointFact(
        kind="makefile",
        path=MAKEFILE,
        line=scan.line,
        name=TEST_TARGET,
        state=state,
        reason=reason,
    )
    return _MakefileEntry(fact, profiles)


def _makefile_state(
    scan: _TargetScan, config: NextestConfig, profiles: list[str]
) -> tuple[str, str]:
    """Return the state a `test` scan proves, with the reason a reviewer needs."""
    if scan.test == "nextest":
        profiles.append(scan.profile or DEFAULT_PROFILE)
        if not scan.proven:
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


def _abandons_order(step: dict[str, object]) -> bool:
    """Return whether a step's `if:` runs it whatever earlier steps did."""
    condition = step.get("if")
    if not isinstance(condition, str):
        return False
    joined = condition.replace(" ", "")
    return any(marker in joined for marker in ("always()", "failure()", "cancelled()"))


def _condition_proves_order(step: dict[str, object]) -> bool:
    """Return whether a step's `if:` leaves it running on every run.

    A step guarded by `runner.os == 'Linux'` skips entirely elsewhere, and the
    test step that follows then starts against a cold cache. GitHub's own
    default is `success()`, so an explicit `success()` is the one spelling
    that still proves the order; anything else the reader cannot evaluate.

    Returns
    -------
    bool
        True when the step carries no `if:`, or carries exactly `success()`.
    """
    condition = step.get("if")
    if condition is None:
        return True
    return isinstance(condition, str) and condition.strip() == "success()"


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
    gated: bool,
    shell: ShellRule,
    make_entry: _MakefileEntry,
    config: NextestConfig,
    profiles: list[str],
) -> tuple[str, str]:
    """Return the prefetch state and reason for one workflow test command."""
    inherited = _inherited_state(command.meaning, make_entry, config, profiles)
    if gated:
        return BEFORE, "an earlier step runs cargo fetch --locked"
    if gates_fetch(commands, position):
        return BEFORE, "cargo fetch --locked runs first in the same step"
    if inherited == BEFORE:
        return BEFORE, "the delegated test target fetches first"
    if has_expression:
        return UNCERTAIN, "part of the step's command is an expression"
    if inherited == UNCERTAIN:
        return UNCERTAIN, "the delegated test target could not be read"
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
    gated: bool,
    shell: ShellRule,
    make_entry: _MakefileEntry,
    config: NextestConfig,
    profiles: list[str],
) -> _StepScan:
    """Classify one `run` step, returning its facts and whether it fetches first."""
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
    if has_expression and not visible.strip():
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
                gated=fetches and not _abandons_order(step),
                shell=effective_shell(step, job, workflow, runs_on),
                make_entry=make_entry,
                config=config,
                profiles=profiles,
            )
            facts.extend(scan.facts)
            if (
                scan.fetches
                and not _allows_failure(step)
                and _condition_proves_order(step)
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
    config = read_nextest(checkout, problems)
    entry = _makefile_entry(checkout, problems, config)
    profiles: list[str] = list(entry.profiles)
    workflows = _workflow_entries(checkout, root, problems, entry, config, profiles)
    facts = [] if entry.fact is None else [entry.fact]
    selected = list(dict.fromkeys(profiles))
    return facts + _nextest_facts(config, selected) + workflows
