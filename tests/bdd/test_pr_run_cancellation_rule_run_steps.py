"""Behavioural tests for `concordat artefact rule run` on pr-run-cancellation.

These scenarios run the real policy over real workflow documents through
`cli.main`, so they cover the whole boundary an operator meets: a checkout on
disk, the envelope built from it, Conftest's verdict, the process exit status
and the rendered table. The Rego suite proves the clauses; this proves the
command delivers them, and that an advisory finding does not fail the run.
"""

from __future__ import annotations

import re
import typing as typ

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from concordat import cli

from .conftest import RunResult

if typ.TYPE_CHECKING:
    import pathlib

scenarios("features/pr_run_cancellation_rule_run.feature")

RULE_ID: typ.Final = "pr-run-cancellation"

_REFERENCE_WORKFLOW: typ.Final = """\
name: CI
on:
  pull_request:
concurrency:
  group: ${{ github.workflow }}-${{ github.event.pull_request.number || github.run_id }}
  cancel-in-progress: ${{ github.event_name == 'pull_request' }}
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - run: echo ok
"""

_JOB_LEVEL_WORKFLOW: typ.Final = """\
name: CI
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    concurrency:
      group: ci-test-${{ github.head_ref || github.run_id }}
      cancel-in-progress: true
    steps:
      - run: echo ok
  lint:
    runs-on: ubuntu-latest
    concurrency:
      group: ci-lint-${{ github.head_ref || github.run_id }}
      cancel-in-progress: true
    steps:
      - run: echo ok
  skipped:
    if: false
    runs-on: ubuntu-latest
    steps:
      - run: echo ok
"""

_WITHOUT_CONCURRENCY_WORKFLOW: typ.Final = """\
name: CI
on:
  pull_request:
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - run: echo ok
"""

_SHARED_GROUP_WORKFLOW: typ.Final = """\
name: CI
on: pull_request
concurrency:
  group: ci
  cancel-in-progress: true
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - run: echo ok
"""

_ONE_EVENT_GUARD_WORKFLOW: typ.Final = """\
name: CI
on: [pull_request, pull_request_target]
concurrency:
  group: ${{ github.event_name == 'pull_request' && github.head_ref || 'ci' }}
  cancel-in-progress: true
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - run: echo ok
"""

_MALFORMED_WORKFLOW: typ.Final = "on: [pull_request\n"

_PUSH_GUARD_WORKFLOW: typ.Final = """\
name: CI
on:
  pull_request:
concurrency:
  group: ${{ github.event_name == 'push' && github.head_ref || 'ci' }}
  cancel-in-progress: true
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - run: echo ok
"""


@pytest.fixture
def cancellation_checkout(tmp_path: pathlib.Path) -> pathlib.Path:
    """Provide the checkout directory one scenario audits."""
    return tmp_path / "checkout"


def _write_workflow(checkout: pathlib.Path, text: str) -> None:
    """Write one workflow document into the checkout."""
    workflows = checkout / ".github" / "workflows"
    workflows.mkdir(parents=True, exist_ok=True)
    (workflows / "ci.yml").write_text(text, encoding="utf-8")


@given("a checkout whose workflows cancel superseded pull-request runs")
def given_reference_workflow(cancellation_checkout: pathlib.Path) -> None:
    """Write the reference concurrency shape."""
    _write_workflow(cancellation_checkout, _REFERENCE_WORKFLOW)


@given("a checkout whose jobs each cancel superseded runs")
def given_job_level_workflow(cancellation_checkout: pathlib.Path) -> None:
    """Write a workflow that cancels on each job and skips a disabled one."""
    _write_workflow(cancellation_checkout, _JOB_LEVEL_WORKFLOW)


@given("a checkout whose pull-request workflow has no concurrency")
def given_workflow_without_concurrency(cancellation_checkout: pathlib.Path) -> None:
    """Write a pull-request workflow that neither cancels nor queues."""
    _write_workflow(cancellation_checkout, _WITHOUT_CONCURRENCY_WORKFLOW)


@given("a checkout whose concurrency group spans pull requests")
def given_shared_group_workflow(cancellation_checkout: pathlib.Path) -> None:
    """Write a cancelling workflow whose group names no pull request."""
    _write_workflow(cancellation_checkout, _SHARED_GROUP_WORKFLOW)


@given("a checkout whose push guard falls through to a constant group")
def given_push_guard_workflow(cancellation_checkout: pathlib.Path) -> None:
    """Write a group whose pull-request branch is a constant fallback."""
    _write_workflow(cancellation_checkout, _PUSH_GUARD_WORKFLOW)


@given("a checkout whose guard covers only the first pull-request event")
def given_one_event_guard_workflow(cancellation_checkout: pathlib.Path) -> None:
    """Write a group whose guard holds for one of the workflow's PR events."""
    _write_workflow(cancellation_checkout, _ONE_EVENT_GUARD_WORKFLOW)


@given("a checkout whose workflow is not valid YAML")
def given_malformed_workflow(cancellation_checkout: pathlib.Path) -> None:
    """Write a workflow the reader cannot decode."""
    _write_workflow(cancellation_checkout, _MALFORMED_WORKFLOW)


@given("a checkout whose workflow directory links outside it")
def given_linked_workflow_directory(
    cancellation_checkout: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    """Point the checkout's workflow directory at another tree."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "ci.yml").write_text(_REFERENCE_WORKFLOW, encoding="utf-8")
    github = cancellation_checkout / ".github"
    github.mkdir(parents=True)
    (github / "workflows").symlink_to(outside)


@when("I audit the checkout for pull-request run cancellation")
def when_audit_checkout(
    cancellation_checkout: pathlib.Path,
    cli_invocation: dict[str, RunResult],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Invoke the command and record its output and exit status."""
    try:
        returncode = cli.main(
            ["artefact", "rule", "run", RULE_ID, "--repo", str(cancellation_checkout)],
        )
    except SystemExit as exc:
        returncode = int(exc.code or 0)
    captured = capsys.readouterr()
    cli_invocation["result"] = RunResult(
        stdout=captured.out,
        stderr=captured.err,
        returncode=returncode,
    )


@then(parsers.cfparse("the audit exit status is {code:d}"))
def then_audit_exit_status(cli_invocation: dict[str, RunResult], code: int) -> None:
    """Assert the recorded exit status."""
    result = cli_invocation["result"]
    assert result.returncode == code, result.stderr or result.stdout


@then("the audit table reports zero findings")
def then_audit_clean(cli_invocation: dict[str, RunResult]) -> None:
    """Assert the table names the compliant verdict and no findings."""
    stdout = cli_invocation["result"].stdout
    # `noncompliant` contains `compliant`, so the verdict must match as a whole
    # word or a noncompliant row would satisfy this step.
    assert re.search(r"\bcompliant\b", stdout), stdout
    assert "RC-001" not in stdout, stdout
    assert "RC-002" not in stdout, stdout
    assert "EN-001" not in stdout, stdout


@then(parsers.cfparse('the audit output reports "{rule_id}" for "{workflow}"'))
def then_audit_reports(
    cli_invocation: dict[str, RunResult], rule_id: str, workflow: str
) -> None:
    """Assert the rendered finding names its rule and its workflow."""
    stdout = cli_invocation["result"].stdout
    assert rule_id in stdout, stdout
    assert workflow in stdout, stdout


@then(parsers.cfparse('the audit output reports severity "{severity}"'))
def then_audit_reports_severity(
    cli_invocation: dict[str, RunResult], severity: str
) -> None:
    """Assert the finding is rendered at the configured severity."""
    stdout = cli_invocation["result"].stdout
    assert severity in stdout, stdout


@then(parsers.cfparse('the audit output reports verdict "{verdict}"'))
def then_audit_reports_verdict(
    cli_invocation: dict[str, RunResult], verdict: str
) -> None:
    """Assert the finding states the verdict the policy assigned."""
    stdout = cli_invocation["result"].stdout
    assert re.search(rf"\b{re.escape(verdict)}\b", stdout), stdout


@then("the audit stderr explains that the directory resolves outside the checkout")
def then_audit_stderr_explains_containment(
    cli_invocation: dict[str, RunResult],
) -> None:
    """Assert the operational failure names the refused directory."""
    stderr = cli_invocation["result"].stderr
    assert "resolves outside the checkout" in stderr, stderr
