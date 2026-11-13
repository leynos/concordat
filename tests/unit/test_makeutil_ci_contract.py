"""Contract tests for the Makeutil install and coverage lanes in CI.

Makeutil supplies structured Makefile facts for the contract tests, so every
full-suite job must provision the pinned, digest-verified release. The
pull-request and main coverage lanes stay on the same ratchet.
"""

from __future__ import annotations

import json
import shlex
import subprocess
import typing as typ
from pathlib import Path

from ruamel.yaml import YAML

REPOSITORY_ROOT = Path(__file__).parents[2]
_MAKEUTIL_COMMAND: typ.Final = ("makeutil", "parse", "Makefile")
# makeutil v0.1.2's x86_64 musl release asset, verified against this digest.
_MAKEUTIL_ENVIRONMENT: typ.Final = {
    "MAKEUTIL_VERSION": "0.1.2",
    "MAKEUTIL_ASSET": "makeutil-x86_64-unknown-linux-musl",
    "MAKEUTIL_SHA256": (
        "688c3385ac2f5cb630e8a0ffe640815f366793195d35e89821b918b09531adf8"
    ),
    "MAKEUTIL_RELEASES": "https://github.com/leynos/makeutil/releases/download",
}
_COVERAGE_BASELINE_PYTHON_FILE: typ.Final = ".coverage-baseline.python-v2"
_MAKEUTIL_INSTALL_TOKENS: typ.Final = (
    "uv",
    "run",
    "scripts/install_release_binary.py",
    "install",
    "makeutil",
)


def _makefile_report() -> dict[str, object]:
    """Return Makeutil's complete, successfully parsed Makefile report."""
    completed = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - Fixed parser command.
        _MAKEUTIL_COMMAND,
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


def _mapping(value: object, *, subject: str) -> dict[str, object]:
    """Return a JSON object, naming the unexpected ``subject`` on failure."""
    assert isinstance(value, dict), f"expected {subject} to be a JSON object"
    return typ.cast("dict[str, object]", value)


def _objects(value: object, *, subject: str) -> list[dict[str, object]]:
    """Return a JSON-object array, naming the unexpected ``subject``."""
    assert isinstance(value, list), f"expected {subject} to be a JSON array"
    return [_mapping(item, subject=f"{subject} item") for item in value]


def _text_sequence(value: object, *, subject: str) -> tuple[str, ...]:
    """Return a JSON string array, naming the unexpected ``subject``."""
    assert isinstance(value, list), f"expected {subject} to be a JSON array"
    assert all(isinstance(item, str) for item in value), (
        f"expected {subject} to contain only JSON strings"
    )
    return tuple(typ.cast("list[str]", value))


def _sole_recipe_rule(target: str) -> dict[str, object]:
    """Return the only parsed recipe-bearing rule for ``target``."""
    rules = _objects(_makefile_report().get("rules"), subject="rules")
    matches = [
        rule
        for rule in rules
        if target in _text_sequence(rule.get("targets"), subject="rule targets")
        and _objects(rule.get("recipes"), subject="rule recipes")
    ]
    assert len(matches) == 1, (
        f"expected one recipe-bearing Makefile rule named {target!r}, found "
        f"{len(matches)}"
    )
    return matches[0]


def _workflow_job(workflow_path: str, job_name: str) -> dict[str, object]:
    """Return the named job from a repository workflow."""
    yaml = YAML(typ="safe")
    workflow = _mapping(
        yaml.load((REPOSITORY_ROOT / workflow_path).read_text()),
        subject=f"{workflow_path} workflow",
    )
    jobs = _mapping(workflow.get("jobs"), subject=f"{workflow_path} jobs")
    return _mapping(jobs.get(job_name), subject=f"{workflow_path} job {job_name!r}")


def _sole_workflow_step(
    job_name: str, step_name: str, *, workflow_path: str = ".github/workflows/ci.yml"
) -> dict[str, object]:
    """Return the sole named workflow step from ``job_name``."""
    steps = _objects(
        _workflow_job(workflow_path, job_name).get("steps"),
        subject=f"{workflow_path} job {job_name!r} steps",
    )
    matches = [step for step in steps if step.get("name") == step_name]
    assert len(matches) == 1, (
        f"expected one {step_name!r} step in {workflow_path} job {job_name!r}, "
        f"found {len(matches)}"
    )
    return matches[0]


def _assert_makeutil_installation(command: object, *, contract: str) -> None:
    """Assert that ``command`` installs the pinned, digest-verified release."""
    assert isinstance(command, str), (
        f"{contract} must provide a Makeutil installation shell command"
    )
    assert (
        tuple(shlex.split(command.replace("\\\n", ""))) == _MAKEUTIL_INSTALL_TOKENS
    ), f"{contract} must install makeutil through the digest-verifying script"


def _assert_makeutil_environment(job: dict[str, object], *, contract: str) -> None:
    """Assert that a full-suite job pins Makeutil's release and its digest."""
    environment = _mapping(job.get("env"), subject=f"{contract} environment")
    pinned = {name: environment.get(name) for name in _MAKEUTIL_ENVIRONMENT}
    assert pinned == _MAKEUTIL_ENVIRONMENT, (
        f"{contract} must pin Makeutil release {_MAKEUTIL_ENVIRONMENT}"
    )


def _assert_pull_request_coverage_contract(lint_test: dict[str, object]) -> None:
    """Assert that pull-request coverage stays on the local ratchet."""
    pull_request_coverage = _sole_workflow_step("lint-test", "Generate coverage")
    assert pull_request_coverage.get("if") == "github.event_name == 'pull_request'", (
        "pull-request coverage must run only for pull-request events"
    )
    pull_request_coverage_inputs = _mapping(
        pull_request_coverage.get("with"),
        subject="pull-request coverage action inputs",
    )
    assert pull_request_coverage_inputs.get("pytest-workers") == "", (
        "pull-request coverage must run pytest serially for a stable ratchet"
    )
    assert (
        pull_request_coverage_inputs.get("baseline-python-file")
        == _COVERAGE_BASELINE_PYTHON_FILE
    ), "pull-request coverage must use the reset Python ratchet baseline"
    assert pull_request_coverage_inputs.get("with-ratchet") == "true", (
        "pull-request coverage must enforce the main-derived ratchet"
    )
    pull_request_steps = lint_test.get("steps")
    assert isinstance(pull_request_steps, list), "CI lint-test steps must be a list"
    assert not any(
        "upload-codescene-coverage"
        in str(_mapping(step, subject="CI step").get("uses", ""))
        for step in pull_request_steps
    ), "pull-request CI must not invoke the CodeScene coverage action"
    yaml = YAML(typ="safe")
    workflow = _mapping(
        yaml.load((REPOSITORY_ROOT / ".github/workflows/ci.yml").read_text()),
        subject=".github/workflows/ci.yml workflow",
    )
    workflow_environment = workflow.get("env")
    if workflow_environment is not None:
        assert "CS_ACCESS_TOKEN" not in _mapping(
            workflow_environment, subject="CI workflow environment"
        ), "pull-request CI workflow must not receive the CodeScene access token"
    assert "CS_ACCESS_TOKEN" not in str(lint_test), (
        "pull-request CI must not receive the CodeScene access token"
    )


def _assert_main_coverage_contract() -> None:
    """Assert that main publishes the ratchet baseline and CodeScene report."""
    yaml = YAML(typ="safe")
    workflow = _mapping(
        yaml.load(
            (REPOSITORY_ROOT / ".github/workflows/coverage-main.yml").read_text()
        ),
        subject=".github/workflows/coverage-main.yml workflow",
    )
    triggers = _mapping(
        workflow.get("on"), subject=".github/workflows/coverage-main.yml triggers"
    )
    assert triggers == {"push": {"branches": ["main"]}}, (
        "main coverage workflow must trigger only on pushes to main"
    )
    coverage = _workflow_job(".github/workflows/coverage-main.yml", "coverage-upload")
    _assert_makeutil_environment(coverage, contract="main coverage Makeutil contract")
    coverage_parser = _sole_workflow_step(
        "coverage-upload",
        "Install Makefile parser",
        workflow_path=".github/workflows/coverage-main.yml",
    )
    _assert_makeutil_installation(
        coverage_parser.get("run"), contract="main coverage Makeutil-install contract"
    )
    main_coverage = _sole_workflow_step(
        "coverage-upload",
        "Generate coverage",
        workflow_path=".github/workflows/coverage-main.yml",
    )
    main_coverage_inputs = _mapping(
        main_coverage.get("with"), subject="main coverage action inputs"
    )
    assert main_coverage_inputs.get("pytest-workers") == "", (
        "main coverage must run pytest serially for a stable ratchet"
    )
    assert (
        main_coverage_inputs.get("baseline-python-file")
        == _COVERAGE_BASELINE_PYTHON_FILE
    ), "main coverage must use the reset Python ratchet baseline"
    assert main_coverage_inputs.get("with-ratchet") == "true", (
        "main coverage must publish the ratchet baseline"
    )
    main_upload = _sole_workflow_step(
        "coverage-upload",
        "Upload coverage data to CodeScene",
        workflow_path=".github/workflows/coverage-main.yml",
    )
    assert main_upload.get("uses") == (
        "leynos/shared-actions/.github/actions/upload-codescene-coverage@"
        "a5765019912a8ab6882b12db049c7cde635f3a85"
    ), "main coverage must use the pinned CodeScene upload action"
    main_upload_inputs = _mapping(
        main_upload.get("with"), subject="main CodeScene upload inputs"
    )
    assert main_upload_inputs.get("mode") == "upload", (
        "main coverage must be the authoritative CodeScene upload"
    )


def test_ci_installs_makeutil_for_every_full_suite() -> None:
    """Every isolated full pytest suite must provision the pinned parser."""
    prerequisites = _text_sequence(
        _sole_recipe_rule("test").get("prerequisites"), subject="test prerequisites"
    )
    assert "makeutil" in prerequisites, (
        "Make test contract must require Makeutil before running contract tests"
    )
    lint_test = _workflow_job(".github/workflows/ci.yml", "lint-test")
    _assert_makeutil_environment(lint_test, contract="CI lint-test Makeutil contract")
    lint_parser = _sole_workflow_step("lint-test", "Install Makefile parser")
    _assert_makeutil_installation(
        lint_parser.get("run"), contract="CI lint-test Makeutil-install contract"
    )
    lint_step = _sole_workflow_step("lint-test", "Run lint and dead-code detection")
    assert lint_step.get("run") == "make lint", (
        "CI lint step must invoke the shared Makefile lint target"
    )
    _assert_pull_request_coverage_contract(lint_test)
    _assert_main_coverage_contract()
