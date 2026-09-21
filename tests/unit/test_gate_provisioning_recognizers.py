"""The gate-provisioning recognizers, driven directly.

`test_gate_tool_provisioning_contract` judges this repository's workflows and
Makefile. It can only do so honestly if the readers beneath it discriminate,
and a reader exercised solely against files that already comply proves nothing
but its own silence. Every case here is synthetic and is chosen for what the
reader must *refuse*: a command that merely mentions a tool, a command that is
not an installation at all, a job that runs no suite step, and a lane that
installs one executable twice with conflicting specifications.

The properties use the generator as their oracle rather than restating the
implementation, so the two can disagree.
"""

from __future__ import annotations

import typing as typ

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from tests.unit.gate_provisioning_support import (
    Lane,
    commands,
    installed_tool_names,
    job_runs_the_suite,
    provisioning,
)

# metacharacters and variable sigils the extractors deliberately ignore, so a
# generated command means what it reads as.
# A name begins with an alphanumeric: an operand starting with a hyphen is
# indistinguishable from an option on any command line, and no module path or
# executable is spelled that way.
_NAMES: typ.Final = st.from_regex(r"[a-z0-9][a-z0-9._-]{0,11}", fullmatch=True)
_VERSIONS: typ.Final = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz0123456789.", min_size=1, max_size=8
)


def test_a_command_that_only_uses_a_tool_does_not_provision_it() -> None:
    """The recognizer reads an install verb, not a mention of the tool.

    ``ci.yml`` both installs and invokes Conftest. Were a mention enough, the
    invocation alone would satisfy the contract and the publisher's missing
    install would have gone on passing.
    """
    invocation = commands(
        "conftest test --policy platform-standards/tofu/policies examples/*.json\n"
    )
    assert invocation, "the invocation fixture must tokenize to one command"
    assert not installed_tool_names(invocation[0]), (
        "invoking a tool is not installing it"
    )
    installation = commands("go install github.com/open-policy-agent/conftest@v0.52.0")
    assert installed_tool_names(installation[0]) == frozenset({"conftest"}), (
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
        provisioning(conflicting)
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
    expected = {"conftest": ("go", "install", "example.com/conftest@v0.52.0")}
    assert provisioning(agreeing) == expected, (
        "a lane that installs one executable twice with the same command must "
        f"report that one command; expected {expected}"
    )


@given(command=st.lists(_NAMES, min_size=1, max_size=6))
def test_only_an_install_command_provisions_anything(command: list[str]) -> None:
    """Without an install verb, no command provisions anything.

    The oracle is the generator: a command drawn without the verb cannot
    install, whatever its operands spell. This is the direction that matters,
    because a recognizer that fired on any mention of a tool would have
    accepted the publisher's missing install.
    """
    assume("install" not in command)
    assert not installed_tool_names(tuple(command)), (
        f"{command} contains no install verb, so it provisions nothing"
    )


@given(
    segments=st.lists(_NAMES, min_size=1, max_size=3),
    executable=_NAMES,
    version=_VERSIONS,
)
def test_a_module_install_provisions_its_final_segment(
    segments: list[str], executable: str, version: str
) -> None:
    """A module path installs the executable its last segment names.

    The drawn executable is the independent oracle: the property asserts the
    recognizer recovers the name the command was generated to install, rather
    than restating how the path is split. Segments begin with an alphanumeric,
    because an operand starting with a hyphen reads as an option and is
    skipped by design.
    """
    module = "/".join([*segments, executable])
    command = ("go", "install", f"{module}@{version}")
    assert installed_tool_names(command) == frozenset({executable}), (
        f"installing {module}@{version} must provision {executable!r}"
    )


def _install_lane(executable: str, versions: typ.Sequence[str]) -> Lane:
    """Return a synthetic lane installing ``executable`` once per version."""
    return Lane(
        "synthetic.yml",
        "generated",
        {
            "steps": [
                {"run": f"go install example.com/{executable}@{version}\n"}
                for version in versions
            ]
        },
    )


@given(
    executable=_NAMES,
    versions=st.lists(_VERSIONS, min_size=2, max_size=4),
)
def test_duplicate_installs_are_judged_by_agreement_not_by_order(
    executable: str, versions: list[str]
) -> None:
    """Whether duplicates are accepted depends on agreement, never on order.

    The oracle is whether the generated versions are all equal. Keeping the
    first or the last install would make the verdict depend on the order the
    steps happen to appear in, which is exactly the defect this guards.
    """
    lane = _install_lane(executable, versions)
    if len(set(versions)) == 1:
        expected = ("go", "install", f"example.com/{executable}@{versions[0]}")
        assert provisioning(lane) == {executable: expected}, (
            f"identical installs of {executable!r} must report {expected}"
        )
    else:
        with pytest.raises(AssertionError, match="conflicting commands"):
            provisioning(lane)


@given(
    before=st.lists(_NAMES, max_size=3),
    after=st.lists(_NAMES, max_size=3),
)
def test_a_suite_step_is_recognized_wherever_it_sits(
    before: list[str], after: list[str]
) -> None:
    """A job runs the suite if any step does, whatever surrounds it.

    Steps are enumerated rather than positionally assumed, so a lane that
    runs the suite last is as much a lane as one that runs it first.
    """
    assume(not {*before, *after} & {"make", "pytest", "uv"})
    surrounding = [{"run": f"{command}\n"} for command in [*before, *after]]
    quiet: dict[str, object] = {"steps": list(surrounding)}
    assert not job_runs_the_suite(quiet, subject="generated quiet job"), (
        f"none of {before + after} runs the suite"
    )
    steps = [
        *({"run": f"{command}\n"} for command in before),
        {"run": "make test\n"},
        *({"run": f"{command}\n"} for command in after),
    ]
    running: dict[str, object] = {"steps": steps}
    assert job_runs_the_suite(running, subject="generated suite job"), (
        f"a `make test` step after {before} must be recognized"
    )


def test_a_command_that_is_not_an_installation_provisions_nothing() -> None:
    """Only a package manager's install verb counts as provisioning.

    A recognizer that read any command containing `install` would credit a
    lane for `echo install conftest`, or for a tool invoked with a path that
    happens to contain the word. Each case is driven directly, because a
    predicate parametrized over compliant files proves only that it is quiet.
    """
    for script in (
        "echo install conftest\n",
        "conftest test --policy install/policies examples/*.json\n",
        "./install conftest\n",
        "make install\n",
    ):
        command = commands(script)[0]
        assert not installed_tool_names(command), (
            f"{script.strip()!r} is not a package-manager install, so it "
            "provisions nothing"
        )
    installer = commands("uv tool install mbake\n")[0]
    assert installed_tool_names(installer) == frozenset({"mbake"}), (
        "a package manager's install verb must still be recognized"
    )


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
    assert not job_runs_the_suite(publishing_job, subject="synthetic publishing job"), (
        "a job that checks out and builds a release does not run the suite"
    )
    suite_job: dict[str, object] = {
        "steps": [{"name": "Run tests", "run": "make test\n"}]
    }
    assert job_runs_the_suite(suite_job, subject="synthetic suite job"), (
        "a job whose step runs `make test` runs the suite"
    )
