"""Contract tests: every lane that runs the gate provisions the gate's tools.

The pull-request lane and the push-to-main coverage publisher both run the
whole pytest suite, but they were provisioned independently. `ci.yml`
installed Conftest for its policy step and so happened to satisfy the three
rule tests that shell out to it, while `coverage-main.yml` installed only the
Makefile parser. The publisher therefore failed on every push to main from
2026-07-30 onwards with ``conftest is required but was not found on PATH``,
and no pull request could see it: the lane that reports the defect is not the
lane that suffers from it.

The contract below removes the asymmetry as a class rather than as one
missing step:

* the tools the suite requires on `PATH` are **derived from the package**,
  by reading the failure messages `concordat` raises when a tool is absent,
  so a newly required tool is covered the day it is introduced;
* the lanes that run the suite are **enumerated** from
  ``.github/workflows``, so a workflow added later is covered too;
* provisioning is recognized from the shape of an install command, not from
  a step's name, so renaming or merging steps cannot void the contract;
* every lane must provision every required tool at the *same* specification,
  so the two lanes cannot drift to different versions of the same tool.

Each assertion ranges over a collection that is first checked for content. A
contract that ranges over an empty collection is satisfied by deleting the
thing it guards, so the required-tool set and the lane set are asserted to be
non-empty and to contain their known members before compliance is judged.

The extraction helpers are exercised directly against synthetic input as well
as against this repository's workflows. Parametrizing a recognizer over files
that already comply proves only that it does not fire; driving it with a case
it must reject proves that it discriminates.
"""

from __future__ import annotations

import json
import re
import shlex
import shutil
import subprocess
import typing as typ
from pathlib import Path

import pytest
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
_KNOWN_REQUIRED_TOOLS: typ.Final = frozenset({"conftest", "makeutil"})

# The lanes this repository is known to run the suite in. As with the tools,
# these are asserted as members of the enumerated set, not as its whole
# content, so a third lane is covered without editing this list.
_KNOWN_SUITE_LANES: typ.Final = frozenset({
    (".github/workflows/ci.yml", "lint-test"),
    (".github/workflows/coverage-main.yml", "coverage-upload"),
})

# A step runs the whole suite if it invokes the shared coverage action or
# runs pytest through the Makefile. Both spellings are recognized because
# the two are interchangeable for this contract's purpose.
_COVERAGE_ACTION: typ.Final = "generate-coverage@"
_SUITE_COMMANDS: typ.Final = (("make", "test"), ("pytest",), ("uv", "run", "pytest"))

_MAKEFILE_SUITE_TARGET: typ.Final = "test"


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


def _commands(script: str) -> tuple[tuple[str, ...], ...]:
    """Return the shell-like token tuples of each line of a ``run`` script.

    Parameters
    ----------
    script:
        A workflow step's ``run`` body.

    Returns
    -------
        One token tuple per logical command line, with continuations joined
        and comments discarded.
    """
    joined = script.replace("\\\n", " ")
    commands: list[tuple[str, ...]] = []
    for line in joined.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        try:
            tokens = tuple(shlex.split(stripped, comments=True))
        except ValueError:
            # An unbalanced quote is not this contract's concern; a step that
            # cannot be tokenized provisions nothing it can claim credit for.
            continue
        if tokens:
            commands.append(tokens)
    return tuple(commands)


def _installed_tool_names(command: typ.Sequence[str]) -> frozenset[str]:
    """Return the executable names one install command provisions.

    A command provisions a tool only if it contains an ``install`` verb.
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
    if "install" not in command:
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


def _steps(job: dict[str, object], *, subject: str) -> list[dict[str, object]]:
    """Return a job's steps as mappings."""
    steps = job.get("steps")
    assert isinstance(steps, list), f"expected {subject} steps to be a list"
    return [_mapping(step, subject=f"{subject} step") for step in steps]


def _step_runs_the_suite(step: dict[str, object]) -> bool:
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
        for command in _commands(run)
        for prefix in _SUITE_COMMANDS
    )


def _runs_the_suite(job: dict[str, object], *, subject: str) -> bool:
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
    return any(_step_runs_the_suite(step) for step in _steps(job, subject=subject))


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
            if _runs_the_suite(job, subject=subject):
                lanes.append(Lane(relative, job_name, job))
    assert lanes, (
        "no workflow job was recognized as running the pytest suite, so the "
        "provisioning contract below would pass vacuously"
    )
    return tuple(lanes)


def _shell_commands(lane: Lane) -> typ.Iterator[tuple[str, ...]]:
    """Yield every shell command a lane's steps run, in step order.

    Parameters
    ----------
    lane:
        The suite-running job to read.

    Yields
    ------
        One token tuple per logical command line across the job's steps.
    """
    for step in _steps(lane.job, subject=str(lane)):
        run = step.get("run")
        if isinstance(run, str):
            yield from _commands(run)


def _provisioning(lane: Lane) -> dict[str, tuple[str, ...]]:
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
        for command in _shell_commands(lane)
        for name in _installed_tool_names(command)
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


def _make_prerequisites(target: str) -> frozenset[str]:
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


def test_required_tools_are_derived_from_the_package() -> None:
    """The required-tool set is read from the package, not restated here."""
    discovered = required_tools()
    assert discovered, (
        "no 'required but was not found on PATH' message was found in "
        f"{PACKAGE_DIRECTORY.name}; the provisioning contract would range "
        "over an empty set"
    )
    missing = _KNOWN_REQUIRED_TOOLS - discovered
    assert not missing, (
        f"the derivation no longer finds {sorted(missing)}, which the suite "
        "still shells out to; fix the derivation rather than the expectation"
    )


def test_suite_lanes_are_enumerated_from_the_workflows() -> None:
    """Both known suite lanes are found by enumeration, not by name."""
    found = {(lane.workflow, lane.job_name) for lane in suite_lanes()}
    missing = _KNOWN_SUITE_LANES - found
    assert not missing, (
        f"these jobs run the pytest suite but were not recognized: "
        f"{sorted(missing)}; found {sorted(found)}"
    )


def test_every_lane_that_runs_the_suite_provisions_the_suites_tools() -> None:
    """A lane that runs the gate must install every tool the gate needs.

    This is the defect that kept the coverage publisher red: it ran the whole
    suite while installing only the Makefile parser.
    """
    needed = required_tools()
    unprovisioned = {
        str(lane): sorted(needed - frozenset(_provisioning(lane)))
        for lane in suite_lanes()
        if needed - frozenset(_provisioning(lane))
    }
    assert not unprovisioned, (
        "every lane that runs the pytest suite must install the tools the "
        f"suite shells out to; these lanes are missing tools: {unprovisioned}"
    )


def test_suite_lanes_provision_the_shared_tools_identically() -> None:
    """Two lanes running one suite must not install two versions of a tool."""
    lanes = suite_lanes()
    commands: dict[str, dict[str, tuple[str, ...]]] = {}
    for lane in lanes:
        for name, command in _provisioning(lane).items():
            if name in required_tools():
                commands.setdefault(name, {})[str(lane)] = command
    assert commands, (
        "no required tool was found to be installed by any lane, so the "
        "agreement assertion below would pass vacuously"
    )
    divergent = {
        name: by_lane
        for name, by_lane in commands.items()
        if len(set(by_lane.values())) > 1
    }
    assert not divergent, (
        "the suite lanes must pin one version of each shared tool; these "
        f"tools are installed differently: {divergent}"
    )


def test_the_make_gate_verifies_the_suites_tools() -> None:
    """The local gate refuses to run the suite without the suite's tools.

    A developer running ``make test`` without Conftest saw three unrelated
    rule tests fail rather than one named missing tool.
    """
    needed = required_tools()
    prerequisites = _make_prerequisites(_MAKEFILE_SUITE_TARGET)
    missing = needed - prerequisites
    assert not missing, (
        f"`make {_MAKEFILE_SUITE_TARGET}` runs the suite, so it must require "
        f"{sorted(missing)} before running it"
    )


def test_a_command_that_only_uses_a_tool_does_not_provision_it() -> None:
    """The recognizer reads an install verb, not a mention of the tool.

    ``ci.yml`` both installs and invokes Conftest. Were a mention enough, the
    invocation alone would satisfy the contract and the publisher's missing
    install would have gone on passing.
    """
    invocation = _commands(
        "conftest test --policy platform-standards/tofu/policies examples/*.json\n"
    )
    assert invocation, "the invocation fixture must tokenize to one command"
    assert not _installed_tool_names(invocation[0]), (
        "invoking a tool is not installing it"
    )
    installation = _commands("go install github.com/open-policy-agent/conftest@v0.52.0")
    assert _installed_tool_names(installation[0]) == frozenset({"conftest"}), (
        "a Go module install must provision the module's final segment"
    )


def test_a_lane_installing_one_tool_twice_differently_is_rejected() -> None:
    """Conflicting duplicate installs are refused rather than silently ranked.

    Keeping the first install would let a lane run a later version while the
    cross-lane comparison judged the earlier command, so the two lanes would
    read as agreeing while running different tools.
    """
    conflicting = Lane(
        "synthetic.yml",
        "conflicting",
        {
            "steps": [
                {"run": "go install example.com/conftest@v0.52.0\n"},
                {"run": "go install example.com/conftest@v0.53.0\n"},
            ]
        },
    )
    with pytest.raises(AssertionError, match="conflicting commands"):
        _provisioning(conflicting)
    agreeing = Lane(
        "synthetic.yml",
        "agreeing",
        {
            "steps": [
                {"run": "go install example.com/conftest@v0.52.0\n"},
                {"run": "go install example.com/conftest@v0.52.0\n"},
            ]
        },
    )
    assert _provisioning(agreeing) == {
        "conftest": ("go", "install", "example.com/conftest@v0.52.0")
    }


def test_a_job_that_does_not_run_the_suite_is_not_a_lane() -> None:
    """The lane recognizer reads the steps, not the presence of a job.

    A contract that treated every job as a suite lane would demand the gate's
    tools of jobs that never run the gate, and would be relaxed to silence.
    """
    publishing_job: dict[str, object] = {
        "steps": [
            {"name": "Check out repository", "uses": "actions/checkout@0000000"},
            {"name": "Publish", "run": "make build-release\n"},
        ]
    }
    assert not _runs_the_suite(publishing_job, subject="synthetic publishing job")
    suite_job: dict[str, object] = {
        "steps": [{"name": "Run tests", "run": "make test\n"}]
    }
    assert _runs_the_suite(suite_job, subject="synthetic suite job")
