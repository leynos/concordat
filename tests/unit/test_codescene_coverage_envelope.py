"""Specify the decoded workflow facts for CodeScene coverage policy checks."""

from __future__ import annotations

import typing as typ

from concordat.rules import codescene_coverage_envelope
from concordat.rules.codescene_coverage_envelope import (
    ENVELOPE_KIND,
    CoverageEnvelope,
    WorkflowFile,
    build_codescene_coverage_envelope,
)

if typ.TYPE_CHECKING:
    import pathlib

    import pytest


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

    def test_rejects_symlinked_workflow_without_reading_target(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Record a symlink as an error without following its target."""
        workflows = tmp_path / ".github" / "workflows"
        workflows.mkdir(parents=True)
        target = tmp_path / "workflow-target.yml"
        target.write_text("secret: do-not-leak\n", encoding="utf-8")
        (workflows / "ci.yml").symlink_to(target)

        read_paths: list[pathlib.Path] = []

        def record_read(path: pathlib.Path) -> str:
            read_paths.append(path)
            raise AssertionError

        monkeypatch.setattr(codescene_coverage_envelope, "_read_text", record_read)

        envelope = build_codescene_coverage_envelope(tmp_path)

        fact = _workflows(envelope)[0]
        assert read_paths == [], read_paths
        assert fact["parsed"] is None, fact
        assert fact["error"] == "workflow file is a symlink", fact
        assert "do-not-leak" not in repr(envelope), envelope

    def test_carries_recursive_yaml_as_json_conversion_error(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Preserve recursive YAML as an error instead of aborting the build."""
        workflows = tmp_path / ".github" / "workflows"
        workflows.mkdir(parents=True)
        (workflows / "ci.yml").write_text(
            "on: pull_request\nloop: &loop\n  self: *loop\n", encoding="utf-8"
        )

        envelope = build_codescene_coverage_envelope(tmp_path)

        fact = _workflows(envelope)[0]
        assert fact["parsed"] is None, fact
        assert str(fact["error"]).startswith("workflow document is not JSON-safe:"), (
            fact
        )
        assert "Circular reference detected" in str(fact["error"]), fact

    def test_carries_json_conversion_type_error_as_error(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Preserve a JSON type failure as an indeterminate workflow fact."""
        workflows = tmp_path / ".github" / "workflows"
        workflows.mkdir(parents=True)
        (workflows / "ci.yml").write_text("on: pull_request\n", encoding="utf-8")
        monkeypatch.setattr(
            codescene_coverage_envelope,
            "_json_safe",
            lambda _: (_ for _ in ()).throw(TypeError("unsupported value")),
        )

        envelope = build_codescene_coverage_envelope(tmp_path)

        fact = _workflows(envelope)[0]
        assert fact["parsed"] is None, fact
        assert fact["error"] == (
            "workflow document is not JSON-safe: unsupported value"
        ), fact
