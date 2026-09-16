"""Specify dispatch for the main-owned CodeScene coverage rule package."""

from __future__ import annotations

import json
import pathlib
import subprocess
import typing as typ

from concordat.rules import runner
from concordat.rules.codescene_coverage_envelope import (
    ENVELOPE_KIND,
    build_codescene_coverage_envelope,
)

if typ.TYPE_CHECKING:
    import pytest_mock


def test_codescene_coverage_manifest_selects_its_workflow_envelope() -> None:
    """Use the decoded workflow envelope declared by the shipped manifest."""
    package = runner._rule_package_dir("main-owned-codescene-coverage")

    builder = runner._envelope_builder(package)

    assert builder is build_codescene_coverage_envelope
    assert ENVELOPE_KIND in runner.ENVELOPE_BUILDERS


def test_runner_sends_the_codescene_coverage_envelope_to_conftest(
    tmp_path: pathlib.Path,
    mocker: pytest_mock.MockFixture,
) -> None:
    """Write decoded workflow facts for the package selected by its manifest."""
    checkout = tmp_path / "checkout"
    workflows = checkout / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "coverage.yaml").write_text(
        "on: pull_request\njobs: {}\n", encoding="utf-8"
    )
    observed: dict[str, object] = {}

    def capture(argv: list[str], _rule_id: str) -> subprocess.CompletedProcess[str]:
        """Record the temporary input document and return a clean policy result."""
        envelope_path = pathlib.Path(argv[-1])
        observed["envelope"] = json.loads(envelope_path.read_text(encoding="utf-8"))
        return subprocess.CompletedProcess(
            argv,
            0,
            json.dumps([{"failures": []}]),
            "",
        )

    mocker.patch.object(runner, "_run_conftest", side_effect=capture)

    result = runner.run_rule("main-owned-codescene-coverage", checkout)

    assert result.verdict == "compliant", result
    envelope = typ.cast("dict[str, object]", observed["envelope"])
    assert envelope["kind"] == ENVELOPE_KIND, envelope
    assert "cargo" not in envelope, envelope
