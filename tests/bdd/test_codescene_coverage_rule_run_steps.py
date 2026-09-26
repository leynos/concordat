"""Behavioural tests for `concordat artefact rule run` on CV-005.

These scenarios run the real policy over real workflow documents through
`cli.main`, so they cover the whole boundary an operator meets: a checkout on
disk, the envelope built from it, Conftest's verdict, the process exit status
and the rendered table. The Rego suite proves the clauses; this proves the
command delivers them.
"""

from __future__ import annotations

import typing as typ

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from concordat import cli

from .conftest import RunResult

if typ.TYPE_CHECKING:
    import pathlib

scenarios("features/codescene_coverage_rule_run.feature")

RULE_ID: typ.Final = "main-owned-codescene-coverage"

_PR_WORKFLOW: typ.Final = """\
on:
  pull_request:
jobs:
  coverage:
    runs-on: ubuntu-latest
    steps:
      - uses: leynos/shared-actions/.github/actions/generate-coverage@0000000
        with:
          with-ratchet: 'true'
          publish-artefact: 'false'
"""

_CODESCENE_STEP: typ.Final = """\
      - uses: leynos/shared-actions/.github/actions/upload-codescene-coverage@0000000
        with:
          mode: check
"""

_MAIN_WORKFLOW: typ.Final = """\
on:
  push:
    branches: [main]
  workflow_dispatch:
concurrency:
  group: coverage-main-${{{{ github.ref }}}}
  cancel-in-progress: false
jobs:
  coverage-upload:
    runs-on: ubuntu-latest
    steps:
      - uses: leynos/shared-actions/.github/actions/generate-coverage@0000000
        with:
          with-ratchet: 'true'
      - id: codescene-token
        run: {check}
      - uses: leynos/shared-actions/.github/actions/upload-codescene-coverage@0000000
        if: {guard}
        with:
          mode: upload
          access-token: ${{{{ secrets.CS_ACCESS_TOKEN }}}}
"""

_STEP_OUTPUT_MAIN_WORKFLOW: typ.Final = """\
on:
  push:
    branches: [main]
  workflow_dispatch:
concurrency:
  group: coverage-main-${{ github.ref }}
  cancel-in-progress: false
jobs:
  coverage-upload:
    runs-on: ubuntu-latest
    steps:
      - uses: leynos/shared-actions/.github/actions/generate-coverage@0000000
        with:
          with-ratchet: 'true'
      - id: codescene-token
        run: |
          echo "available=${{ secrets.CS_ACCESS_TOKEN != '' }}" >> "$GITHUB_OUTPUT"
      - uses: leynos/shared-actions/.github/actions/upload-codescene-coverage@0000000
        if: >-
          ${{ steps.codescene-token.outputs.available == 'true' &&
          github.ref == 'refs/heads/main' }}
        with:
          mode: upload
          access-token: ${{ secrets.CS_ACCESS_TOKEN }}
"""

_CHECK_COMMAND: typ.Final = (
    'echo "available=${{ secrets.CS_ACCESS_TOKEN != \'\' }}" >> "$GITHUB_OUTPUT"'
)
_AVAILABLE_OUTPUT: typ.Final = "steps.codescene-token.outputs.available == 'true'"
_GUARD: typ.Final = (
    "${{ github.ref == 'refs/heads/main' && " + _AVAILABLE_OUTPUT + " }}"
)
_CREDENTIAL_ONLY_GUARD: typ.Final = "${{ " + _AVAILABLE_OUTPUT + " }}"


@pytest.fixture
def coverage_checkout(tmp_path: pathlib.Path) -> pathlib.Path:
    """Provide the checkout directory one scenario audits."""
    return tmp_path / "checkout"


def _write_workflows(
    checkout: pathlib.Path, *, pull_request: str, main: str | None
) -> None:
    """Write one checkout's workflow documents."""
    workflows = checkout / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "ci.yml").write_text(pull_request, encoding="utf-8")
    if main is not None:
        (workflows / "coverage-main.yml").write_text(main, encoding="utf-8")


@given("a checkout whose coverage wiring satisfies CV-005")
def given_compliant_checkout(coverage_checkout: pathlib.Path) -> None:
    """Write a checkout that every clause accepts."""
    _write_workflows(
        coverage_checkout,
        pull_request=_PR_WORKFLOW,
        main=_MAIN_WORKFLOW.format(guard=_GUARD, check=_CHECK_COMMAND),
    )


@given("a checkout whose publisher uses a block-scalar token output guard")
def given_step_output_guard_checkout(coverage_checkout: pathlib.Path) -> None:
    """Write the guarded publisher as an operator would write its YAML."""
    _write_workflows(
        coverage_checkout,
        pull_request=_PR_WORKFLOW,
        main=_STEP_OUTPUT_MAIN_WORKFLOW,
    )


@given("a checkout whose pull-request lane invokes CodeScene")
def given_pr_lane_invokes_codescene(coverage_checkout: pathlib.Path) -> None:
    """Write a checkout whose pull-request lane runs a CodeScene check."""
    _write_workflows(
        coverage_checkout,
        pull_request=_PR_WORKFLOW + _CODESCENE_STEP,
        main=_MAIN_WORKFLOW.format(guard=_GUARD, check=_CHECK_COMMAND),
    )


@given("a checkout whose publisher upload step has no ref guard")
def given_publisher_without_ref_guard(coverage_checkout: pathlib.Path) -> None:
    """Write a checkout whose publisher is guarded on the credential alone."""
    _write_workflows(
        coverage_checkout,
        pull_request=_PR_WORKFLOW,
        main=_MAIN_WORKFLOW.format(guard=_CREDENTIAL_ONLY_GUARD, check=_CHECK_COMMAND),
    )


@given("a checkout whose workflow directory links outside it")
def given_linked_workflow_directory(
    coverage_checkout: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    """Point the checkout's workflow directory at another tree."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "ci.yml").write_text(_PR_WORKFLOW, encoding="utf-8")
    github = coverage_checkout / ".github"
    github.mkdir(parents=True)
    (github / "workflows").symlink_to(outside)


@when("I audit the checkout for CodeScene coverage")
def when_audit_checkout(
    coverage_checkout: pathlib.Path,
    cli_invocation: dict[str, RunResult],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Invoke the command and record its output and exit status."""
    try:
        returncode = cli.main(
            ["artefact", "rule", "run", RULE_ID, "--repo", str(coverage_checkout)],
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
    assert "compliant" in stdout, stdout
    assert "CV-005" not in stdout, stdout


@then(parsers.cfparse('the audit output reports "{message}" for "{workflow}"'))
def then_audit_reports(
    cli_invocation: dict[str, RunResult], message: str, workflow: str
) -> None:
    """Assert the rendered finding names its message and its workflow."""
    stdout = cli_invocation["result"].stdout
    assert "CV-005" in stdout, stdout
    assert message in stdout, stdout
    assert workflow in stdout, stdout


@then("the audit stderr explains that the directory resolves outside the checkout")
def then_audit_stderr_explains_containment(
    cli_invocation: dict[str, RunResult],
) -> None:
    """Assert the operational failure names the refused directory."""
    stderr = cli_invocation["result"].stderr
    assert "resolves outside the checkout" in stderr, stderr
