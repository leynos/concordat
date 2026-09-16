"""Specify the decoded workflow facts for CodeScene coverage policy checks."""

from __future__ import annotations

import typing as typ

from concordat.rules.codescene_coverage_envelope import (
    ENVELOPE_KIND,
    CoverageEnvelope,
    WorkflowFile,
    build_codescene_coverage_envelope,
)

if typ.TYPE_CHECKING:
    import pathlib


def _workflows(envelope: CoverageEnvelope) -> list[WorkflowFile]:
    """Return the workflow facts from one policy envelope."""
    return envelope["workflows"]


class TestBuildCodesceneCoverageEnvelope:
    """Record every local workflow without interpreting GitHub Actions syntax."""

    def test_decodes_both_yaml_extensions_in_filename_order(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Include each workflow as decoded YAML evidence for the policy."""
        workflows = tmp_path / ".github" / "workflows"
        workflows.mkdir(parents=True)
        workflow = "name: test\non: pull_request\njobs: {}\n"
        (workflows / "coverage.yaml").write_text(workflow, encoding="utf-8")
        (workflows / "ci.yml").write_text(workflow, encoding="utf-8")
        (workflows / "README.md").write_text("not YAML\n", encoding="utf-8")

        envelope = build_codescene_coverage_envelope(tmp_path)

        assert envelope["kind"] == ENVELOPE_KIND, envelope
        assert [fact["path"] for fact in _workflows(envelope)] == [
            ".github/workflows/ci.yml",
            ".github/workflows/coverage.yaml",
        ]
        assert all(fact["error"] is None for fact in _workflows(envelope))

    def test_carries_malformed_workflow_as_indeterminate_evidence(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Preserve a decode error instead of dropping an unreadable workflow."""
        workflows = tmp_path / ".github" / "workflows"
        workflows.mkdir(parents=True)
        (workflows / "ci.yml").write_text("on: [pull_request\n", encoding="utf-8")

        envelope = build_codescene_coverage_envelope(tmp_path)

        fact = _workflows(envelope)[0]
        assert fact["parsed"] is None, fact
        assert "invalid YAML" in str(fact["error"]), fact
