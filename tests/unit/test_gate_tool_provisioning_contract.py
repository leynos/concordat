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
* a tool must be installed **before** the step that runs the suite, since
  installing it afterwards fails exactly as the publisher did;
* every lane must provision every required tool at the *same* specification,
  so the two lanes cannot drift to different versions of the same tool.

Each assertion ranges over a collection that is first checked for content. A
contract that ranges over an empty collection is satisfied by deleting the
thing it guards, so the required-tool set and the lane set are asserted to be
non-empty and to contain their known members before compliance is judged.

The readers are in `gate_provisioning_support`, and are driven directly
against synthetic input in `test_gate_provisioning_recognizers`. Parametrizing
a recognizer over files that already comply proves only that it does not fire;
driving it with a case it must reject proves that it discriminates.
"""

from __future__ import annotations

import typing as typ

from tests.unit.gate_provisioning_support import (
    GO_SETUP_ACTION,
    KNOWN_REQUIRED_TOOLS,
    KNOWN_SUITE_LANES,
    MAKEFILE_SUITE_TARGET,
    MISSING_TOOL_REFUSAL,
    PACKAGE_DIRECTORY,
    installed_tool_names,
    installer_program,
    job_steps,
    late_installs,
    make_prerequisites,
    provisioning,
    required_tools,
    run_make_target,
    shell_commands,
    suite_lanes,
    suite_step_index,
)

if typ.TYPE_CHECKING:
    from pathlib import Path


def test_required_tools_are_derived_from_the_package() -> None:
    """The required-tool set is read from the package, not restated here."""
    discovered = required_tools()
    assert discovered, (
        "no 'required but was not found on PATH' message was found in "
        f"{PACKAGE_DIRECTORY.name}; the provisioning contract would range "
        "over an empty set"
    )
    missing = KNOWN_REQUIRED_TOOLS - discovered
    assert not missing, (
        f"the derivation no longer finds {sorted(missing)}, which the suite "
        "still shells out to; fix the derivation rather than the expectation"
    )


def test_suite_lanes_are_enumerated_from_the_workflows() -> None:
    """Both known suite lanes are found by enumeration, not by name."""
    found = {(lane.workflow, lane.job_name) for lane in suite_lanes()}
    missing = KNOWN_SUITE_LANES - found
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
        str(lane): sorted(needed - frozenset(provisioning(lane)))
        for lane in suite_lanes()
        if needed - frozenset(provisioning(lane))
    }
    assert not unprovisioned, (
        "every lane that runs the pytest suite must install the tools the "
        f"suite shells out to; these lanes are missing tools: {unprovisioned}"
    )


def test_every_tool_is_installed_before_the_suite_runs() -> None:
    """Installing a tool after the gate is the same as not installing it.

    Provisioning judged only by name passes for a lane that installs Conftest
    after the coverage step, which fails exactly as the publisher did. An
    install in the *same* step as the suite is refused too: whether it runs
    before or after cannot be read from the step list, and a contract that
    cannot tell should not say yes.
    """
    needed = required_tools()
    late = {}
    for lane in suite_lanes():
        overdue = late_installs(lane, needed)
        if overdue:
            late[str(lane)] = {
                "suite step": suite_step_index(lane),
                "installed at": overdue,
            }
    assert not late, (
        "every required tool must be installed before the step that runs the "
        f"suite; these lanes install one too late: {late}"
    )


def test_a_go_install_is_preceded_by_the_shared_go_setup() -> None:
    """A lane installing with Go must set Go up, at the pin the others use.

    The runners carry no Go toolchain the publisher can rely on, so the
    install step alone is not provisioning: without the setup action the
    command fails before it installs anything. A lane that sets Go up twice
    at different pins is refused rather than judged by either one, since the
    toolchain its installs actually run under is then not readable and the
    comparison across lanes would compare the wrong value.
    """
    setups: dict[str, str] = {}
    for lane in suite_lanes():
        go_installs = [
            index
            for index, command in shell_commands(lane)
            if installer_program(command) == "go" and installed_tool_names(command)
        ]
        if not go_installs:
            continue
        references = {
            index: uses
            for index, step in enumerate(job_steps(lane.job, subject=str(lane)))
            if isinstance(uses := step.get("uses"), str)
            and uses.startswith(GO_SETUP_ACTION)
        }
        assert references, (
            f"{lane} installs with Go but never runs {GO_SETUP_ACTION}, so "
            "the install has no toolchain to run under"
        )
        assert len(set(references.values())) == 1, (
            f"{lane} sets Go up more than once at different pins, so which "
            f"toolchain its installs run under is not readable: {references}"
        )
        earliest_setup = min(references)
        assert earliest_setup < min(go_installs), (
            f"{lane} sets Go up at step {earliest_setup}, after its first Go "
            f"install at step {min(go_installs)}; the install would run "
            "without a toolchain"
        )
        setups[str(lane)] = references[earliest_setup]
    assert setups, (
        "no lane was found installing with Go, so the agreement assertion "
        "below would pass vacuously"
    )
    assert len(set(setups.values())) == 1, (
        f"every lane must set Go up at the same pin; found {setups}"
    )


def test_suite_lanes_provision_the_shared_tools_identically() -> None:
    """Two lanes running one suite must not install two versions of a tool."""
    lanes = suite_lanes()
    commands: dict[str, dict[str, tuple[str, ...]]] = {}
    for lane in lanes:
        for name, command in provisioning(lane).items():
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
    prerequisites = make_prerequisites(MAKEFILE_SUITE_TARGET)
    missing = needed - prerequisites
    assert not missing, (
        f"`make {MAKEFILE_SUITE_TARGET}` runs the suite, so it must require "
        f"{sorted(missing)} before running it"
    )


def test_each_tool_target_refuses_when_the_tool_is_absent(
    tmp_path: Path,
) -> None:
    """Every required tool has a target that actually checks for it.

    Asserting only that a tool names a prerequisite of `test` passes with the
    target's recipe emptied, which would let `make test` run without the tool
    and fail later in unrelated rule tests. Each target is therefore driven
    both ways: with nothing on `PATH` it must refuse and name the tool, and
    with a stub of that name it must succeed.
    """
    for tool in sorted(required_tools()):
        empty = tmp_path / f"{tool}-absent"
        empty.mkdir()
        absent = run_make_target(tool, empty)
        assert absent.returncode != 0, (
            f"`make {tool}` must refuse when {tool} is not on PATH; it exited "
            f"{absent.returncode}"
        )
        assert MISSING_TOOL_REFUSAL in absent.stderr, (
            f"`make {tool}` must refuse with the shared message; stderr was "
            f"{absent.stderr!r}"
        )
        assert tool in absent.stderr, (
            f"`make {tool}` must name the missing tool; stderr was {absent.stderr!r}"
        )
        stubbed = tmp_path / f"{tool}-present"
        stubbed.mkdir()
        stub = stubbed / tool
        stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        stub.chmod(0o755)
        present = run_make_target(tool, stubbed)
        assert present.returncode == 0, (
            f"`make {tool}` must accept a {tool} on PATH; it exited "
            f"{present.returncode} with {present.stderr!r}"
        )
