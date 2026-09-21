"""The repository's own Markdown wiring must satisfy the rule it publishes.

Concordat owns the `markdown-formatting-baseline` canon, so its `Makefile`,
its `.markdownlint-cli2.jsonc`, and its CI workflow are the rule's first
consumer. The policy suite proves the sensor against synthetic fixtures; this
module proves the checked-in wiring itself, through the production builder
and real Conftest, and then pins the parts of the workflow the sensor cannot
see: the token scope, the formatter's version floor, and the exact revisions
the two installing steps resolve.

Each workflow contract asserts the value that would break, not the name of
the step that carries it. A contract satisfied by the step's presence passes
with the step's substance deleted.
"""

from __future__ import annotations

import pathlib
import re
import typing as typ

import pytest
from ruamel.yaml import YAML

from concordat.rules import runner
from concordat.rules.markdown_envelope import build_markdown_envelope

_RULE_ID: typ.Final = "markdown-formatting-baseline"
REPO_ROOT: typ.Final = pathlib.Path(__file__).resolve().parents[2]
CI_WORKFLOW: typ.Final = REPO_ROOT / ".github" / "workflows" / "ci.yml"
USERS_GUIDE: typ.Final = REPO_ROOT / "docs" / "users-guide.md"

# `mdtablefix --check` and `--git` first appear in 0.6.0; an older build
# fails both recipes at once, so the floor is part of the wiring.
MDTABLEFIX_FLOOR: typ.Final = "0.6.0"
MARKDOWNLINT_ACTION: typ.Final = "DavidAnson/markdownlint-cli2-action"
INSTALL_MDTABLEFIX_ACTION: typ.Final = (
    "leynos/shared-actions/.github/actions/install-mdtablefix"
)

_ACTION_PIN = re.compile(r"DavidAnson/markdownlint-cli2-action@([0-9a-zA-Z._-]+)")
_FULL_SHA = re.compile(r"^[0-9a-f]{40}$")

_yaml = YAML(typ="safe")


def _pins(path: pathlib.Path) -> list[str]:
    """Return every markdownlint action reference *path* carries.

    Returns
    -------
    list[str]
        The references, in the order they appear.
    """
    return _ACTION_PIN.findall(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def lint_test_job() -> dict[str, object]:
    """Return the decoded `lint-test` job from the CI workflow.

    Returns
    -------
    dict[str, object]
        The job mapping.
    """
    workflow = typ.cast(
        "dict[str, object]", _yaml.load(CI_WORKFLOW.read_text(encoding="utf-8"))
    )
    jobs = typ.cast("dict[str, dict[str, object]]", workflow["jobs"])
    return jobs["lint-test"]


def _steps(job: dict[str, object]) -> list[dict[str, object]]:
    """Return the job's steps.

    Returns
    -------
    list[dict[str, object]]
        Every step mapping, in order.
    """
    return typ.cast("list[dict[str, object]]", job["steps"])


def _step_using(job: dict[str, object], action: str) -> dict[str, object]:
    """Return the one step whose `uses` names *action*.

    Returns
    -------
    dict[str, object]
        The matching step.
    """
    matches = [
        step
        for step in _steps(job)
        if isinstance(step.get("uses"), str)
        and typ.cast("str", step["uses"]).startswith(f"{action}@")
    ]
    assert len(matches) == 1, matches
    return matches[0]


@pytest.mark.timeout(120)
def test_repository_satisfies_its_own_markdown_baseline() -> None:
    """Running the shipped rule over this checkout reports no findings.

    This is the wiring change the rule exists to mandate: `fmt` and
    `check-fmt` calling the tools directly, the canonical markdownlint
    configuration, and CI linting only through the pinned action.
    """
    envelope = build_markdown_envelope(REPO_ROOT)
    results = runner._invoke_conftest(_RULE_ID, envelope)
    findings = runner._findings_from_results(results)
    assert list(findings) == [], findings


def test_ci_pins_the_action_to_a_full_commit_sha() -> None:
    """The workflow's pin is forty hex characters, as PD-006 requires."""
    pins = _pins(CI_WORKFLOW)
    assert len(pins) == 1, pins
    assert _FULL_SHA.fullmatch(pins[0]), pins[0]


def test_the_documented_pin_matches_the_workflow() -> None:
    """The users' guide snippet and CI name the same revision.

    A reader copies the snippet into their own workflow, so a snippet that
    drifts from the pin this repository actually runs propagates the stale
    revision across the estate.
    """
    documented = _pins(USERS_GUIDE)
    assert documented == _pins(CI_WORKFLOW), documented


def test_the_lint_job_token_is_read_only(lint_test_job: dict[str, object]) -> None:
    """`lint-test` grants exactly `contents: read` and nothing else.

    Asserting the mapping's whole contents rather than one key: a second
    grant added later would pass a membership test.
    """
    assert lint_test_job["permissions"] == {"contents": "read"}, lint_test_job


def test_the_workflow_pins_the_mdtablefix_version_floor(
    lint_test_job: dict[str, object],
) -> None:
    """CI installs the first release carrying `--check` and `--git`.

    The value is asserted, not the presence of the variable: an empty or
    absent version installs whatever the action defaults to, and the two
    Markdown recipes fail against anything older.
    """
    env = typ.cast("dict[str, object]", lint_test_job["env"])
    assert env["MDTABLEFIX_VERSION"] == MDTABLEFIX_FLOOR, env


def test_the_installing_step_receives_that_version(
    lint_test_job: dict[str, object],
) -> None:
    """The floor reaches the action rather than sitting unread in `env`."""
    step = _step_using(lint_test_job, INSTALL_MDTABLEFIX_ACTION)
    inputs = typ.cast("dict[str, object]", step["with"])
    assert inputs["version"] == "${{ env.MDTABLEFIX_VERSION }}", step


@pytest.mark.parametrize(
    "action",
    [
        pytest.param(MARKDOWNLINT_ACTION, id="markdownlint"),
        pytest.param(INSTALL_MDTABLEFIX_ACTION, id="install-mdtablefix"),
    ],
)
def test_the_markdown_steps_pin_full_revisions(
    lint_test_job: dict[str, object], action: str
) -> None:
    """Neither Markdown step resolves a tag or a branch at run time."""
    step = _step_using(lint_test_job, action)
    reference = typ.cast("str", step["uses"]).split("@", 1)[1]
    assert _FULL_SHA.fullmatch(reference), reference


def test_the_linting_step_covers_every_markdown_file(
    lint_test_job: dict[str, object],
) -> None:
    """The action lints `**/*.md`, matching `make markdownlint`.

    A narrower glob would leave documents linted locally and unlinted in
    continuous integration, which is the divergence PD-006 exists to stop.
    """
    step = _step_using(lint_test_job, MARKDOWNLINT_ACTION)
    inputs = typ.cast("dict[str, object]", step["with"])
    assert inputs["globs"] == "**/*.md", step


def test_no_shell_step_lints_or_installs_the_linter(
    lint_test_job: dict[str, object],
) -> None:
    """No `run:` step names markdownlint-cli2 in command position.

    PD-006 reads this from the envelope too, but only for a workflow it can
    decode; asserting it here keeps the repository's own gate honest if the
    sensor's grammar ever loosens.
    """
    scripts = [
        typ.cast("str", step["run"])
        for step in _steps(lint_test_job)
        if isinstance(step.get("run"), str)
    ]
    assert scripts, lint_test_job
    offenders = [script for script in scripts if "markdownlint-cli2" in script]
    assert offenders == [], offenders
