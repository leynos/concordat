"""Check the cancellation and CodeScene coverage rules against one checkout.

leynos/concordat#388 asks for a dual-trigger workflow that satisfies both the
cancellation rule and the existing coverage topology. The coverage rule keeps
CodeScene publication in a main-only workflow, so one document cannot be both.
These tests evaluate both real policies over one checkout to pin that
reconciliation: a main-only publisher plus a dual-trigger workflow passes
both, while moving CodeScene into the dual-trigger workflow passes
cancellation and is reported by the coverage rule.
"""

from __future__ import annotations

import typing as typ

from concordat.rules import runner

if typ.TYPE_CHECKING:
    import pathlib

CANCELLATION_RULE = "pr-run-cancellation"
COVERAGE_RULE = "main-owned-codescene-coverage"

MAIN_PUBLISHER = """\
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
    environment: codescene
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

DUAL_TRIGGER = """\
on: [push, pull_request]
concurrency:
  group: ${{ github.workflow }}-${{ github.event.pull_request.number || github.run_id }}
  cancel-in-progress: ${{ github.event_name == 'pull_request' }}
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - run: cargo test
"""

DUAL_TRIGGER_WITH_CODESCENE = """\
on: [push, pull_request]
concurrency:
  group: ${{ github.workflow }}-${{ github.event.pull_request.number || github.run_id }}
  cancel-in-progress: ${{ github.event_name == 'pull_request' }}
jobs:
  coverage:
    runs-on: ubuntu-latest
    steps:
      - uses: leynos/shared-actions/.github/actions/generate-coverage@0000000
        with:
          with-ratchet: 'true'
          publish-artefact: 'false'
      - uses: leynos/shared-actions/.github/actions/upload-codescene-coverage@0000000
        with:
          mode: check
"""


def _checkout(tmp_path: pathlib.Path, workflows: dict[str, str]) -> pathlib.Path:
    """Write one checkout's root workflow documents."""
    directory = tmp_path / ".github" / "workflows"
    directory.mkdir(parents=True)
    for name, text in workflows.items():
        (directory / name).write_text(text, encoding="utf-8")
    return tmp_path


def test_a_main_publisher_and_a_dual_trigger_workflow_satisfy_both_rules(
    tmp_path: pathlib.Path,
) -> None:
    """The publisher stays main-only; the dual-trigger workflow is not it."""
    checkout = _checkout(
        tmp_path,
        {"coverage.yml": MAIN_PUBLISHER, "ci.yml": DUAL_TRIGGER},
    )

    cancellation = runner.run_rule(CANCELLATION_RULE, checkout)
    coverage = runner.run_rule(COVERAGE_RULE, checkout)

    assert cancellation.verdict == "compliant", cancellation.findings
    assert coverage.verdict == "compliant", coverage.findings


def test_codescene_in_a_dual_trigger_workflow_is_reported_by_the_coverage_rule(
    tmp_path: pathlib.Path,
) -> None:
    """One document cannot serve both rules; the publisher must stay main-only."""
    checkout = _checkout(
        tmp_path,
        {"coverage.yml": MAIN_PUBLISHER, "ci.yml": DUAL_TRIGGER_WITH_CODESCENE},
    )

    cancellation = runner.run_rule(CANCELLATION_RULE, checkout)
    coverage = runner.run_rule(COVERAGE_RULE, checkout)

    assert cancellation.verdict == "compliant", cancellation.findings
    assert coverage.verdict == "noncompliant", coverage.findings
    messages = [finding.message for finding in coverage.findings]
    assert any(
        "pull-request workflow invokes CodeScene" in message for message in messages
    ), messages
