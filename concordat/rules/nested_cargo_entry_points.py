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

import collections.abc as cabc
import dataclasses
import re
import tomllib
import typing as typ

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from .fs_probe import probe_file
from .makefile_expansion import (
    UNRESOLVED_MARK,
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
from .nested_cargo_scripts import (
    TEST_TARGET,
    CommandMeaning,
    ScriptCommand,
    gates_fetch,
    line_fetches,
    script_commands,
)

if typ.TYPE_CHECKING:
    import pathlib

    from .makefile_facts import MakeRecipe, MakeRule, MakeutilReport

OPERATION_READ_WORKFLOW: typ.Final = "read-nested-cargo-workflow"

MAKEFILE: typ.Final = "Makefile"
NEXTEST_CONFIG: typ.Final = ".config/nextest.toml"
DEFAULT_PROFILE: typ.Final = "default"
ALL_FILTER: typ.Final = "all()"
ONESHELL_TARGET: typ.Final = ".ONESHELL"

# The three states a policy acts on.
BEFORE: typ.Final = "before"
ABSENT: typ.Final = "absent"
UNCERTAIN: typ.Final = "uncertain"

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
    """What the `test` target says, accumulated line by line."""

    is_found: bool = False
    test: str | None = None
    profile: str | None = None
    line: int = 1
    proven: bool = False
    proven_reason: str = ""
    uncertain_reason: str = ""


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
        elif command.meaning.is_unreadable:
            scan.uncertain_reason = "a recipe command could not be read"
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


@dataclasses.dataclass(frozen=True, slots=True)
class _NextestProfile:
    """One nextest profile: whether its setup scripts fetch, and where it starts."""

    state: str
    line: int


@dataclasses.dataclass(frozen=True, slots=True)
class _NextestConfig:
    """The decoded `.config/nextest.toml`, or why it could not be decoded.

    ``is_present`` says a configuration file is there to read. A repository
    without one has no setup scripts and no profile rows; its nextest runs are
    still test entry points, but the configuration is not one of them.
    """

    profiles: dict[str, _NextestProfile]
    is_readable: bool
    is_present: bool

    def profile(self, name: str) -> _NextestProfile:
        """Return what *name* declares, defaulting to an absent prefetch.

        Returns
        -------
        _NextestProfile
            The profile's state and the line its declaration starts on.
        """
        return self.profiles.get(name, _NextestProfile(state=ABSENT, line=1))

    def state(self, name: str | None) -> str:
        """Return the prefetch state of the selected profile.

        No `--profile` flag selects nextest's own `default` profile, so a
        missing name is an ordinary selection rather than an unknown one. A
        name the expansion could not settle is the unknown case.

        Returns
        -------
        str
            One of `before`, `absent` or `uncertain`.
        """
        if not self.is_readable or (name is not None and UNRESOLVED_MARK in name):
            return UNCERTAIN
        return self.profile(name or DEFAULT_PROFILE).state


def _setup_fetches(setup: object) -> bool:
    """Return whether a nextest setup entry runs `cargo fetch --locked`."""
    commands = [setup] if isinstance(setup, str) else setup
    if not isinstance(commands, list):
        return False
    text = "\n".join(str(command) for command in commands)
    return any(command.meaning.is_prefetch for command in script_commands(text))


def _profile_state(body: cabc.Mapping[str, object]) -> str:
    """Return whether a profile's setup scripts fetch before every test."""
    scripts = body.get("scripts")
    if not isinstance(scripts, list):
        return ABSENT
    for entry in scripts:
        if not isinstance(entry, cabc.Mapping):
            continue
        # Only `all()` covers every test in the profile; a narrower filter
        # leaves the tests outside it with a cold cache.
        if str(entry.get("filter", "")).strip() != ALL_FILTER:
            continue
        if _setup_fetches(entry.get("setup")):
            return BEFORE
    return ABSENT


def _script_line(text: str, profile: str) -> int:
    """Return the 1-based line a profile's setup scripts are declared on.

    The `scripts` array table is where a missing setup script would be added,
    so that line is the one a reviewer needs. A profile that declares none
    still appears as `[profile.<name>]`, and that heading is the next best
    place to look; a profile named nowhere points at the file itself.

    Returns
    -------
    int
        The line number, or 1 when the profile is named nowhere.
    """
    headings = (f"[[profile.{profile}.scripts]]", f"[profile.{profile}]")
    for heading in headings:
        for number, line in enumerate(text.splitlines(), start=1):
            if line.strip() == heading:
                return number
    return 1


def _nextest_config(
    profiles: dict[str, _NextestProfile], *, is_readable: bool, is_present: bool
) -> _NextestConfig:
    """Return a nextest configuration with every flag spelled out."""
    return _NextestConfig(profiles, is_readable=is_readable, is_present=is_present)


def _read_nextest(checkout: pathlib.Path, problems: list[str]) -> _NextestConfig:
    """Decode `.config/nextest.toml`, recording a problem rather than guessing.

    Returns
    -------
    _NextestConfig
        Each profile's setup scripts, and whether the file was there and could
        be read.
    """
    path = checkout / NEXTEST_CONFIG
    probe = probe_file(path)
    if probe.read_error is not None:
        problems.append(f"{NEXTEST_CONFIG}: {probe.read_error}")
        return _nextest_config({}, is_readable=False, is_present=True)
    if not probe.present:
        return _nextest_config({}, is_readable=True, is_present=False)
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        problems.append(f"cannot read {NEXTEST_CONFIG}: {error}")
        return _nextest_config({}, is_readable=False, is_present=True)
    try:
        parsed: object = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        problems.append(f"cannot decode {NEXTEST_CONFIG}: {error}")
        return _nextest_config({}, is_readable=False, is_present=True)
    profiles_table = parsed.get("profile") if isinstance(parsed, dict) else None
    if not isinstance(profiles_table, dict):
        return _nextest_config({}, is_readable=True, is_present=True)
    profiles = {
        str(name): _NextestProfile(
            state=_profile_state(body) if isinstance(body, dict) else ABSENT,
            line=_script_line(text, str(name)),
        )
        for name, body in profiles_table.items()
    }
    return _nextest_config(profiles, is_readable=True, is_present=True)


@dataclasses.dataclass(frozen=True, slots=True)
class _MakefileEntry:
    """The Makefile's `test` entry point, and the nextest profiles it selects."""

    fact: EntryPointFact | None
    profiles: list[str]


def _makefile_entry(
    checkout: pathlib.Path, problems: list[str], config: _NextestConfig
) -> _MakefileEntry:
    """Return the Makefile's `test` entry point, if it has a test command."""
    path = checkout / MAKEFILE
    if not path.is_file():
        return _MakefileEntry(None, [])
    try:
        facts = inspect_makefile(path)
    except MakefileRefusedError as error:
        problems.append(f"cannot parse {MAKEFILE}: {error}")
        return _MakefileEntry(None, [])
    report = facts.report
    values = single_assignments(report["variables"])
    index = _rules_by_target(report)
    if TEST_TARGET not in index:
        return _MakefileEntry(None, [])
    scan = _scan_target(index, values, sequencing_holds=ONESHELL_TARGET not in index)
    if not scan.is_found:
        return _MakefileEntry(None, [])
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
    scan: _TargetScan, config: _NextestConfig, profiles: list[str]
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


def _node_line(node: object, fallback: int) -> int:
    """Return the 1-based source line of a YAML node, or *fallback*."""
    location = getattr(node, "lc", None)
    line = getattr(location, "line", None)
    return line + 1 if isinstance(line, int) else fallback


def _inherited_state(
    meaning: CommandMeaning,
    make_entry: EntryPointFact | None,
    config: _NextestConfig,
    profiles: list[str],
) -> str:
    """Return what a delegated `make test` or `cargo nextest run` already proves."""
    if meaning.test == "make":
        return ABSENT if make_entry is None else make_entry["state"]
    if meaning.test == "nextest":
        profiles.append(meaning.profile or DEFAULT_PROFILE)
        return config.state(meaning.profile)
    return ABSENT


def _step_state(
    command: ScriptCommand,
    position: int,
    commands: list[ScriptCommand],
    *,
    has_expression: bool,
    gated: bool,
    make_entry: EntryPointFact | None,
    config: _NextestConfig,
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
    make_entry: EntryPointFact | None,
    config: _NextestConfig,
    profiles: list[str],
) -> _StepScan:
    """Classify one `run` step, returning its facts and whether it fetches first."""
    visible, has_expression = _visible(run)
    commands = script_commands(visible)
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
    make_entry: EntryPointFact | None,
    config: _NextestConfig,
    profiles: list[str],
) -> list[EntryPointFact]:
    """Return the entry points of every job in one decoded workflow."""
    jobs = _mapping(parsed.get("jobs"))
    if jobs is None:
        return []
    facts: list[EntryPointFact] = []
    for job_name, raw_job in jobs.items():
        job = _mapping(raw_job)
        if job is None:
            continue
        steps = job.get("steps")
        if not isinstance(steps, list):
            continue
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
                make_entry=make_entry,
                config=config,
                profiles=profiles,
            )
            facts.extend(scan.facts)
            if scan.fetches and not _allows_failure(step):
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
    make_entry: EntryPointFact | None,
    config: _NextestConfig,
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


def _nextest_facts(config: _NextestConfig, profiles: list[str]) -> list[EntryPointFact]:
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
        reason = {
            BEFORE: "an all() setup script runs cargo fetch --locked",
            UNCERTAIN: "the nextest configuration could not be read",
        }.get(state, "no all() setup script runs cargo fetch --locked")
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
    config = _read_nextest(checkout, problems)
    entry = _makefile_entry(checkout, problems, config)
    profiles: list[str] = list(entry.profiles)
    workflows = _workflow_entries(
        checkout, root, problems, entry.fact, config, profiles
    )
    facts = [] if entry.fact is None else [entry.fact]
    selected = list(dict.fromkeys(profiles))
    return facts + _nextest_facts(config, selected) + workflows
