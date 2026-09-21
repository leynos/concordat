"""Readers for the gate-provisioning contracts.

The contracts in `test_gate_tool_provisioning_contract` and
`test_gate_provisioning_recognizers` share one set of readers: the tools the
package requires on `PATH`, the workflow jobs that run the suite, what each
job installs and where, and a runner for one Make target under a controlled
search path. They live here so both modules judge the same repository through
the same eyes, and so neither grows past the point where a reader can hold it.

Nothing here asserts a repository fact. These are the eyes; the contracts are
the judgements.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import typing as typ
from pathlib import Path

from ruamel.yaml import YAML

REPOSITORY_ROOT: typ.Final = Path(__file__).parents[2]
WORKFLOW_DIRECTORY: typ.Final = REPOSITORY_ROOT / ".github/workflows"
PACKAGE_DIRECTORY: typ.Final = REPOSITORY_ROOT / "concordat"

# `concordat` reports a missing external tool with one fixed phrase, so the
# set of tools the suite needs on PATH is readable from the package itself.
_MISSING_TOOL_MESSAGE: typ.Final = re.compile(
    r"\b([a-z][a-z0-9_.-]*) is required but was not found on PATH"
)

# The tools this repository is known to require. Naming them keeps a silent
# regression in the derivation above loud: a derivation that suddenly finds
# nothing would otherwise make every assertion below vacuous.
KNOWN_REQUIRED_TOOLS: typ.Final = frozenset({"conftest", "makeutil"})

# The lanes this repository is known to run the suite in. As with the tools,
# these are asserted as members of the enumerated set, not as its whole
# content, so a third lane is covered without editing this list.
KNOWN_SUITE_LANES: typ.Final = frozenset({
    (".github/workflows/ci.yml", "lint-test"),
    (".github/workflows/coverage-main.yml", "coverage-upload"),
})

# A step runs the whole suite if it invokes the shared coverage action or
# runs pytest through the Makefile. Both spellings are recognized because
# the two are interchangeable for this contract's purpose.
_COVERAGE_ACTION: typ.Final = "generate-coverage@"
_SUITE_COMMANDS: typ.Final = (("make", "test"), ("pytest",), ("uv", "run", "pytest"))

MAKEFILE_SUITE_TARGET: typ.Final = "test"

# The package managers whose `install` verb provisions an executable. A
# command run by anything else is not an installation, however its arguments
# read, so `echo install conftest` provisions nothing.
_INSTALLERS: typ.Final = frozenset({
    "apt-get",
    "cargo",
    "go",
    "npm",
    "pip",
    "pipx",
    "rustup",
    "uv",
})
_ENVIRONMENT_PREFIX: typ.Final = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=.*", re.DOTALL)

# The shell control operators that separate one simple command from the
# next. A suite run or an install may follow any of them.
_OPERATOR_CHARACTERS: typ.Final = frozenset({"&", "|", ";"})

# The action that must precede a `go install`, since the publisher has no Go
# toolchain of its own.
GO_SETUP_ACTION: typ.Final = "actions/setup-go@"

# `ensure_tool`'s refusal, as the Makefile spells it. Asserting the message
# rather than the exit status alone keeps the target from passing by failing
# for some other reason.
MISSING_TOOL_REFUSAL: typ.Final = "is required, but not installed"


class Lane(typ.NamedTuple):
    """One workflow job that runs the full pytest suite."""

    workflow: str
    job_name: str
    job: dict[str, object]

    def __str__(self) -> str:
        """Return the lane's repository-relative identity."""
        return f"{self.workflow} job {self.job_name!r}"


def _mapping(value: object, *, subject: str) -> dict[str, object]:
    """Return a mapping, naming the unexpected ``subject`` on failure."""
    assert isinstance(value, dict), f"expected {subject} to be a mapping"
    return typ.cast("dict[str, object]", value)


def _workflow_files() -> tuple[Path, ...]:
    """Return this repository's own workflow files, sorted for stable failures.

    Returns
    -------
        Every ``.yml`` and ``.yaml`` file in this repository's workflow
        directory, in sorted order.
    """
    workflows = tuple(
        sorted(
            path
            for pattern in ("*.yml", "*.yaml")
            for path in WORKFLOW_DIRECTORY.glob(pattern)
        )
    )
    assert workflows, (
        "no workflow files were found, so every contract below would pass vacuously"
    )
    return workflows


def required_tools() -> frozenset[str]:
    """Return the external tools `concordat` requires on `PATH`.

    Returns
    -------
        Every tool named by a "required but was not found on PATH" message in
        the package source.
    """
    return frozenset(
        match.group(1)
        for path in sorted(PACKAGE_DIRECTORY.rglob("*.py"))
        for match in _MISSING_TOOL_MESSAGE.finditer(path.read_text(encoding="utf-8"))
    )


def _end_of_quote(line: str, index: int) -> int:
    """Return the index just past the quoted string opening at ``index``."""
    quote = line[index]
    index += 1
    while index < len(line):
        if quote == '"' and line[index] == "\\":
            index += 2
            continue
        if line[index] == quote:
            return index + 1
        index += 1
    return len(line)


def _end_of_operator(line: str, index: int) -> int:
    """Return the index just past the control operator at ``index``."""
    while index < len(line) and line[index] in _OPERATOR_CHARACTERS:
        index += 1
    return index


def split_compound(line: str) -> tuple[str, ...]:
    """Split one shell line at its unquoted control operators.

    A workflow step may chain commands with ``&&``, ``||``, ``;`` or a pipe,
    and the suite or an install may sit after any of them. Splitting is done
    on the raw text rather than on tokens, because a quoted ``"&&"`` is an
    argument and tokenizing first would make the two indistinguishable.

    Parameters
    ----------
    line:
        One physical line of a step's ``run`` body.

    Returns
    -------
        The line's segments, each of which is one simple command.
    """
    segments: list[str] = []
    start = 0
    index = 0
    while index < len(line):
        character = line[index]
        if character == "\\":
            index = min(index + 2, len(line))
        elif character in "'\"":
            index = _end_of_quote(line, index)
        elif character in _OPERATOR_CHARACTERS:
            segments.append(line[start:index])
            index = _end_of_operator(line, index)
            start = index
        else:
            index += 1
    segments.append(line[start:])
    return tuple(segment for segment in (s.strip() for s in segments) if segment)


def _tokenize(segment: str) -> tuple[str, ...]:
    """Return one simple command's tokens, or none if it cannot be read.

    An unbalanced quote is not this contract's concern: a segment that
    cannot be tokenized provisions nothing it can claim credit for.

    Returns
    -------
        The segment's tokens, empty when it cannot be tokenized.
    """
    try:
        return tuple(shlex.split(segment, comments=True))
    except ValueError:
        return ()


def _line_commands(line: str) -> tuple[tuple[str, ...], ...]:
    """Return the simple commands one physical line runs."""
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return ()
    return tuple(
        tokens for segment in split_compound(stripped) if (tokens := _tokenize(segment))
    )


def commands(script: str) -> tuple[tuple[str, ...], ...]:
    """Return the shell-like token tuples of each command in a ``run`` script.

    Parameters
    ----------
    script:
        A workflow step's ``run`` body.

    Returns
    -------
        One token tuple per simple command, with continuations joined,
        comments discarded and control operators split on.
    """
    joined = script.replace("\\\n", " ")
    return tuple(
        command for line in joined.splitlines() for command in _line_commands(line)
    )


def installer_program(command: typ.Sequence[str]) -> str | None:
    """Return the package manager a command runs, ignoring `NAME=value` prefixes.

    Parameters
    ----------
    command:
        One tokenized command line.

    Returns
    -------
        The program name, or `None` for a command that runs no program.
    """
    for token in command:
        if _ENVIRONMENT_PREFIX.fullmatch(token):
            continue
        return token
    return None


def installed_tool_names(command: typ.Sequence[str]) -> frozenset[str]:
    """Return the executable names one install command provisions.

    A command provisions a tool only if a known package manager runs it with
    an ``install`` verb. Requiring the program keeps the recognizer erring
    towards not recognizing: a false positive is silent, and would let a lane
    claim credit for an install it never performs, while a false negative
    fails the contract loudly and is fixed by naming the manager here.
    Arguments are reduced to the executable they would leave on `PATH`: a Go
    module path becomes its final segment and a version suffix is dropped, so
    ``go install github.com/open-policy-agent/conftest@v0.52.0`` provisions
    ``conftest``.

    Parameters
    ----------
    command:
        One tokenized command line.

    Returns
    -------
        The executable names the command installs, which is empty for any
        command that is not an installation.
    """
    if installer_program(command) not in _INSTALLERS or "install" not in command:
        return frozenset()
    operands = command[command.index("install") + 1 :]
    names: set[str] = set()
    for operand in operands:
        if operand.startswith("-") or "$" in operand:
            continue
        candidate = operand.split("@", 1)[0].rsplit("/", 1)[-1]
        if candidate:
            names.add(candidate)
    return frozenset(names)


def _job_environment(job: dict[str, object]) -> dict[str, str]:
    """Return a job's literal string environment, ignoring expressions."""
    environment = job.get("env")
    if environment is None:
        return {}
    return {
        name: value
        for name, value in _mapping(environment, subject="job environment").items()
        if isinstance(value, str)
    }


def _expand(token: str, environment: dict[str, str]) -> str:
    """Return ``token`` with ``${NAME}`` and ``$NAME`` job variables resolved."""
    for name, value in environment.items():
        token = token.replace(f"${{{name}}}", value).replace(f"${name}", value)
    return token


def job_steps(job: dict[str, object], *, subject: str) -> list[dict[str, object]]:
    """Return a job's steps as mappings."""
    steps = job.get("steps")
    assert isinstance(steps, list), f"expected {subject} steps to be a list"
    return [_mapping(step, subject=f"{subject} step") for step in steps]


def step_runs_the_suite(step: dict[str, object]) -> bool:
    """Return whether one step runs the whole pytest suite.

    Returns
    -------
        Whether the step invokes the shared coverage action or runs pytest.
    """
    uses = step.get("uses")
    if isinstance(uses, str) and _COVERAGE_ACTION in uses:
        return True
    run = step.get("run")
    if not isinstance(run, str):
        return False
    return any(
        tuple(command[: len(prefix)]) == prefix
        for command in commands(run)
        for prefix in _SUITE_COMMANDS
    )


def job_runs_the_suite(job: dict[str, object], *, subject: str) -> bool:
    """Return whether a job runs the whole pytest suite.

    A job that calls a reusable workflow has no steps of its own. Such a job
    is not a lane here: this repository calls only external reusable
    workflows, none of which runs its suite. A job with neither steps nor a
    reusable-workflow call is malformed and is reported rather than skipped.

    Returns
    -------
        Whether any of the job's steps runs the full suite.
    """
    if job.get("steps") is None:
        assert "uses" in job, (
            f"{subject} has neither steps nor a reusable-workflow call, so it "
            "cannot be judged for suite provisioning"
        )
        return False
    return any(step_runs_the_suite(step) for step in job_steps(job, subject=subject))


def suite_lanes() -> tuple[Lane, ...]:
    """Return every workflow job that runs the full pytest suite.

    Returns
    -------
        One lane per suite-running job, ordered by workflow then job name.
    """
    yaml = YAML(typ="safe")
    lanes: list[Lane] = []
    for path in _workflow_files():
        relative = path.relative_to(REPOSITORY_ROOT).as_posix()
        workflow = _mapping(
            yaml.load(path.read_text(encoding="utf-8")), subject=f"{relative} workflow"
        )
        jobs = _mapping(workflow.get("jobs"), subject=f"{relative} jobs")
        for job_name in sorted(jobs):
            job = _mapping(jobs[job_name], subject=f"{relative} job {job_name!r}")
            subject = f"{relative} job {job_name!r}"
            if job_runs_the_suite(job, subject=subject):
                lanes.append(Lane(relative, job_name, job))
    assert lanes, (
        "no workflow job was recognized as running the pytest suite, so the "
        "provisioning contract below would pass vacuously"
    )
    return tuple(lanes)


def shell_commands(lane: Lane) -> typ.Iterator[tuple[int, tuple[str, ...]]]:
    """Yield every shell command a lane's steps run, with its step position.

    Parameters
    ----------
    lane:
        The suite-running job to read.

    Yields
    ------
        The step's index and one token tuple per logical command line, in
        step order, so an install can be placed relative to the suite step.
    """
    for index, step in enumerate(job_steps(lane.job, subject=str(lane))):
        run = step.get("run")
        if isinstance(run, str):
            for command in commands(run):
                yield index, command


def suite_step_index(lane: Lane) -> int:
    """Return the position of the first step that runs the suite.

    Parameters
    ----------
    lane:
        The suite-running job to read.

    Returns
    -------
        The index of the earliest suite-running step.

    Raises
    ------
    AssertionError
        If the lane runs no suite step, which the enumeration rules out.
    """
    for index, step in enumerate(job_steps(lane.job, subject=str(lane))):
        if step_runs_the_suite(step):
            return index
    message = f"{lane} was enumerated as a suite lane but runs no suite step"
    raise AssertionError(message)


def provisioning_positions(lane: Lane) -> dict[str, int]:
    """Return the step index at which each tool is last installed.

    The last install is the one whose version reaches `PATH`, so it is the
    one that has to precede the suite step.

    Parameters
    ----------
    lane:
        The suite-running job to read.

    Returns
    -------
        A mapping from executable name to the index of its last install step.
    """
    positions: dict[str, int] = {}
    for index, command in shell_commands(lane):
        for name in installed_tool_names(command):
            positions[name] = index
    return positions


def late_installs(lane: Lane, needed: typ.AbstractSet[str]) -> dict[str, int]:
    """Return the needed tools a lane installs no earlier than the suite step.

    Equality counts as late. Two commands in one step have an order the step
    list cannot show, so a contract that cannot tell whether the install ran
    first should not say that it did.

    Parameters
    ----------
    lane:
        The suite-running job to read.
    needed:
        The tools the suite requires on `PATH`.

    Returns
    -------
        A mapping from tool name to the step index of its last install, for
        every needed tool installed at or after the suite step.
    """
    suite_index = suite_step_index(lane)
    return {
        tool: position
        for tool, position in provisioning_positions(lane).items()
        if tool in needed and position >= suite_index
    }


def provisioning(lane: Lane) -> dict[str, tuple[str, ...]]:
    """Return each tool a lane installs and the command that installs it.

    Parameters
    ----------
    lane:
        The suite-running job to inspect.

    Returns
    -------
        A mapping from executable name to the installing command's tokens,
        with job-level environment variables expanded so that two lanes
        pinning the same revision compare equal. A lane that installs one
        executable twice with different commands is rejected: the last
        install wins on `PATH` while a first-wins reading would compare the
        earlier command across lanes and pass.
    """
    environment = _job_environment(lane.job)
    installs = [
        (name, tuple(_expand(token, environment) for token in command))
        for _, command in shell_commands(lane)
        for name in installed_tool_names(command)
    ]
    provisioning: dict[str, tuple[str, ...]] = {}
    for name, command in installs:
        previous = provisioning.get(name)
        assert previous is None or previous == command, (
            f"{lane} installs {name!r} twice with conflicting commands, so "
            f"which version reaches PATH is not readable from the workflow: "
            f"{previous} and {command}"
        )
        provisioning[name] = command
    return provisioning


def _makefile_report() -> dict[str, object]:
    """Return Makeutil's complete, successfully parsed Makefile report."""
    makeutil = shutil.which("makeutil")
    assert makeutil is not None, (
        "the gate-provisioning contract reads Makefile facts through makeutil, "
        "which `make test` requires on PATH"
    )
    completed = subprocess.run(  # noqa: S603 - Resolved parser path, fixed arguments.
        (makeutil, "parse", "Makefile"),
        capture_output=True,
        check=True,
        cwd=REPOSITORY_ROOT,
        text=True,
    )
    report = typ.cast("dict[str, object]", json.loads(completed.stdout))
    parse = _mapping(report.get("parse"), subject="parse report")
    assert parse.get("status") == "complete", (
        f"makeutil did not complete the Makefile parse: {parse!r}"
    )
    return report


def make_prerequisites(target: str) -> frozenset[str]:
    """Return the prerequisites of the sole recipe-bearing rule for ``target``."""
    rules = _makefile_report().get("rules")
    assert isinstance(rules, list), "expected makeutil rules to be a JSON array"
    matches = [
        rule
        for rule in (_mapping(entry, subject="Makefile rule") for entry in rules)
        if target in typ.cast("list[str]", rule.get("targets", []))
        and rule.get("recipes")
    ]
    assert len(matches) == 1, (
        f"expected one recipe-bearing Makefile rule named {target!r}, found "
        f"{len(matches)}"
    )
    prerequisites = matches[0].get("prerequisites")
    assert isinstance(prerequisites, list), (
        f"expected {target!r} prerequisites to be a JSON array"
    )
    return frozenset(typ.cast("list[str]", prerequisites))


def _make_executable() -> str:
    """Return the resolved Make executable the gate tests drive."""
    executable = shutil.which("make")
    assert executable is not None, (
        "the gate-provisioning contract drives Make targets, so make must be on PATH"
    )
    return executable


def run_make_target(target: str, search_path: Path) -> subprocess.CompletedProcess[str]:
    """Run one Make target with ``search_path`` as the whole of `PATH`.

    Parameters
    ----------
    target:
        The Make target to run.
    search_path:
        The sole directory placed on `PATH`, so the target sees exactly the
        executables written into it.

    Returns
    -------
        The completed process, whatever its exit status.
    """
    make_executable = _make_executable()
    environment = dict(os.environ)
    environment["PATH"] = str(search_path)
    return subprocess.run(  # noqa: S603 - Resolved Make path, fixed target.
        (make_executable, "--no-print-directory", target),
        capture_output=True,
        check=False,
        cwd=REPOSITORY_ROOT,
        env=environment,
        text=True,
    )
