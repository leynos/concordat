"""Unit tests for the Markdown formatting policy-envelope builder.

`build_markdown_envelope` decides what the policy is asked about: whether the
checkout carries Markdown, what its `Makefile`, markdownlint configuration,
and workflows contain, and how a file that exists but cannot be decoded is
carried. Those are `concordat.rules.markdown_envelope`'s own boundary, so
they are tested apart from the `makeutil` parser it delegates to.
"""

from __future__ import annotations

import json
import pathlib
import typing as typ

import pytest

from concordat.errors import OperationalRuleError
from concordat.rules.markdown_envelope import (
    ENVELOPE_KIND,
    MarkdownlintConfig,
    WorkflowFile,
    _has_markdown_files,
    build_markdown_envelope,
)
from tests.unit.rule_test_support import MINIMAL_REPORT

if typ.TYPE_CHECKING:
    from tests.conftest import CmdMox

WORKFLOW = """---
name: CI
on:
  push:
jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: DavidAnson/markdownlint-cli2-action@v24
        with:
          globs: '**/*.md'
"""


def _config(envelope: dict[str, object]) -> MarkdownlintConfig:
    """Return the envelope's markdownlint fact, asserting it is present."""
    fact = envelope["markdownlint"]
    assert fact is not None, envelope
    return typ.cast("MarkdownlintConfig", fact)


def _workflows(envelope: dict[str, object]) -> list[WorkflowFile]:
    """Return the envelope's workflow facts."""
    return typ.cast("list[WorkflowFile]", envelope["workflows"])


class TestApplicability:
    """Markdown presence decides whether the rule applies."""

    def test_empty_checkout_is_not_applicable(self, tmp_path: pathlib.Path) -> None:
        """Nothing to govern yields an inapplicable, fact-free envelope."""
        envelope = build_markdown_envelope(tmp_path)
        assert envelope["kind"] == ENVELOPE_KIND, envelope
        assert envelope["applicability"] == {
            "markdown_files": False,
            "root_makefile": False,
            "markdownlint_config": False,
            "workflows_dir": False,
        }, envelope["applicability"]
        assert envelope["makefile"] is None, envelope
        assert envelope["markdownlint"] is None, envelope
        assert envelope["workflows"] == [], envelope

    def test_nested_markdown_is_detected(self, tmp_path: pathlib.Path) -> None:
        """A Markdown file anywhere outside the pruned directories counts."""
        (tmp_path / "docs" / "guides").mkdir(parents=True)
        (tmp_path / "docs" / "guides" / "intro.markdown").write_text("# Hi\n")
        envelope = build_markdown_envelope(tmp_path)
        assert envelope["applicability"]["markdown_files"] is True, envelope

    @pytest.mark.parametrize(
        "directory",
        [".git", ".venv", "node_modules", "target", ".uv-cache", ".vtcode", "memories"],
    )
    def test_markdown_under_a_pruned_directory_does_not_count(
        self, tmp_path: pathlib.Path, directory: str
    ) -> None:
        """Dependency, build, and cache trees never bring a checkout into scope."""
        (tmp_path / directory / "sub").mkdir(parents=True)
        (tmp_path / directory / "sub" / "README.md").write_text("# Hi\n")
        envelope = build_markdown_envelope(tmp_path)
        assert envelope["applicability"]["markdown_files"] is False, envelope

    def test_a_symlink_alone_does_not_count(self, tmp_path: pathlib.Path) -> None:
        """A link such as `CRUSH.md` is judged by its target, not the link."""
        (tmp_path / "AGENTS.txt").write_text("agents\n")
        (tmp_path / "CRUSH.md").symlink_to(tmp_path / "AGENTS.txt")
        envelope = build_markdown_envelope(tmp_path)
        assert envelope["applicability"]["markdown_files"] is False, envelope


class TestHasMarkdownFiles:
    """Only a regular Markdown file brings a checkout into scope."""

    def test_regular_markdown_file_counts(self, tmp_path: pathlib.Path) -> None:
        """A regular `.md` file is governed Markdown."""
        (tmp_path / "README.md").write_text("# Hi\n")
        assert _has_markdown_files(tmp_path) is True

    def test_non_markdown_file_alone_does_not_count(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A file with another suffix is not Markdown."""
        (tmp_path / "notes.txt").write_text("hi\n")
        assert _has_markdown_files(tmp_path) is False

    def test_symlinked_markdown_alone_does_not_count(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A `.md` symbolic link is judged by its target, not the link."""
        (tmp_path / "AGENTS.txt").write_text("agents\n")
        (tmp_path / "CRUSH.md").symlink_to(tmp_path / "AGENTS.txt")
        assert _has_markdown_files(tmp_path) is False

    def test_regular_markdown_counts_beside_a_symlink(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A regular `.md` file still counts when a `.md` link also exists."""
        (tmp_path / "AGENTS.md").write_text("# Agents\n")
        (tmp_path / "CRUSH.md").symlink_to(tmp_path / "AGENTS.md")
        assert _has_markdown_files(tmp_path) is True


class TestMakefileFacts:
    """The root Makefile is parsed by makeutil exactly as the Rust envelope does."""

    def test_makefile_report_is_forwarded(
        self, tmp_path: pathlib.Path, cmd_mox: CmdMox
    ) -> None:
        """The validated makeutil report becomes the `makefile` fact."""
        (tmp_path / "Makefile").write_text("fmt:\n\tmdtablefix --in-place\n")
        cmd_mox.mock("makeutil").returns(stdout=json.dumps(MINIMAL_REPORT))
        cmd_mox.replay()
        envelope = build_markdown_envelope(tmp_path)
        cmd_mox.verify()
        assert envelope["applicability"]["root_makefile"] is True, envelope
        assert envelope["makefile"] == MINIMAL_REPORT, envelope["makefile"]

    def test_makeutil_failure_is_operational(
        self, tmp_path: pathlib.Path, cmd_mox: CmdMox
    ) -> None:
        """A fatal makeutil exit stops the audit rather than becoming a fact."""
        (tmp_path / "Makefile").write_text("fmt:\n")
        cmd_mox.mock("makeutil").returns(exit_code=2, stderr="boom")
        cmd_mox.replay()
        with pytest.raises(OperationalRuleError, match="boom") as exc_info:
            build_markdown_envelope(tmp_path)
        assert exc_info.value.tool == "makeutil", exc_info.value.tool


class TestMarkdownlintConfig:
    """The JSONC configuration is decoded, or carried with its error."""

    def test_config_is_decoded(self, tmp_path: pathlib.Path) -> None:
        """Comments and trailing commas are accepted and the value recorded."""
        (tmp_path / ".markdownlint-cli2.jsonc").write_text(
            '{ // house rules\n  "config": {"MD004": {"style": "dash"},},\n}\n'
        )
        envelope = build_markdown_envelope(tmp_path)
        fact = _config(typ.cast("dict[str, object]", envelope))
        assert envelope["applicability"]["markdownlint_config"] is True, envelope
        assert fact["path"] == ".markdownlint-cli2.jsonc", fact
        assert fact["parsed"] == {"config": {"MD004": {"style": "dash"}}}, fact
        assert fact["error"] is None, fact

    def test_malformed_config_carries_its_error(self, tmp_path: pathlib.Path) -> None:
        """A file that exists but cannot be decoded is a fact, not an exception."""
        (tmp_path / ".markdownlint-cli2.jsonc").write_text('{"config": {\n')
        fact = _config(typ.cast("dict[str, object]", build_markdown_envelope(tmp_path)))
        assert fact["parsed"] is None, fact
        assert fact["error"] is not None, fact
        assert "line" in fact["error"], fact

    def test_non_utf8_config_carries_its_error(self, tmp_path: pathlib.Path) -> None:
        """Undecodable bytes are reported as a content fact."""
        (tmp_path / ".markdownlint-cli2.jsonc").write_bytes(b"\xff\xfe{}")
        fact = _config(typ.cast("dict[str, object]", build_markdown_envelope(tmp_path)))
        assert fact["parsed"] is None, fact
        assert fact["error"] is not None, fact
        assert "UTF-8" in fact["error"], fact

    def test_alternate_config_names_are_listed(self, tmp_path: pathlib.Path) -> None:
        """Other markdownlint configuration spellings are reported by name."""
        (tmp_path / ".markdownlint.yaml").write_text("MD013: false\n")
        (tmp_path / ".markdownlint-cli2.cjs").write_text("module.exports = {};\n")
        envelope = build_markdown_envelope(tmp_path)
        assert envelope["markdownlint"] is None, envelope
        assert envelope["alternate_markdownlint_configs"] == [
            ".markdownlint-cli2.cjs",
            ".markdownlint.yaml",
        ], envelope

    def test_unreadable_config_is_operational(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A file that cannot be opened at all stops the audit."""
        path = tmp_path / ".markdownlint-cli2.jsonc"
        path.write_text("{}")
        original = pathlib.Path.read_text

        def refuse(
            self: pathlib.Path,
            encoding: str | None = None,
            errors: str | None = None,
            newline: str | None = None,
        ) -> str:
            if self == path:
                raise PermissionError("denied")
            return original(self, encoding=encoding, errors=errors, newline=newline)

        monkeypatch.setattr(pathlib.Path, "read_text", refuse)
        with pytest.raises(OperationalRuleError, match="denied") as exc_info:
            build_markdown_envelope(tmp_path)
        assert exc_info.value.operation == "read-markdownlint-config", exc_info.value


class TestWorkflows:
    """Every workflow under `.github/workflows` is decoded in name order."""

    def test_workflows_are_decoded_in_name_order(self, tmp_path: pathlib.Path) -> None:
        """Only YAML files count, and they are listed sorted by file name."""
        workflows = tmp_path / ".github" / "workflows"
        workflows.mkdir(parents=True)
        (workflows / "release.yml").write_text(WORKFLOW)
        (workflows / "ci.yaml").write_text(WORKFLOW)
        (workflows / "README.md").write_text("# not a workflow\n")
        envelope = build_markdown_envelope(tmp_path)
        facts = _workflows(typ.cast("dict[str, object]", envelope))
        assert envelope["applicability"]["workflows_dir"] is True, envelope
        assert [fact["path"] for fact in facts] == [
            ".github/workflows/ci.yaml",
            ".github/workflows/release.yml",
        ], facts
        parsed = typ.cast("dict[str, object]", facts[0]["parsed"])
        jobs = typ.cast("dict[str, object]", parsed["jobs"])
        assert "lint" in jobs, parsed
        assert all(fact["error"] is None for fact in facts), facts

    def test_yaml_one_two_keeps_on_as_a_string(self, tmp_path: pathlib.Path) -> None:
        """The `on` trigger key is not read as the YAML 1.1 boolean."""
        workflows = tmp_path / ".github" / "workflows"
        workflows.mkdir(parents=True)
        (workflows / "ci.yml").write_text(WORKFLOW)
        fact = _workflows(
            typ.cast("dict[str, object]", build_markdown_envelope(tmp_path))
        )[0]
        parsed = typ.cast("dict[str, object]", fact["parsed"])
        assert "on" in parsed, parsed

    def test_timestamps_are_made_json_safe(self, tmp_path: pathlib.Path) -> None:
        """A YAML timestamp scalar does not break the envelope's JSON encoding."""
        workflows = tmp_path / ".github" / "workflows"
        workflows.mkdir(parents=True)
        (workflows / "ci.yml").write_text("name: CI\nsince: 2026-01-01\njobs: {}\n")
        envelope = build_markdown_envelope(tmp_path)
        assert json.loads(json.dumps(envelope))["workflows"][0]["parsed"]["since"] == (
            "2026-01-01"
        ), envelope

    @pytest.mark.parametrize(
        ("content", "fragment"),
        [
            pytest.param("on: [push\njobs:\n", "invalid YAML", id="syntax"),
            pytest.param("- just\n- a list\n", "not a mapping", id="sequence"),
        ],
    )
    def test_undecodable_workflow_carries_its_error(
        self, tmp_path: pathlib.Path, content: str, fragment: str
    ) -> None:
        """A workflow that cannot be decoded is carried with the reason."""
        workflows = tmp_path / ".github" / "workflows"
        workflows.mkdir(parents=True)
        (workflows / "ci.yml").write_text(content)
        fact = _workflows(
            typ.cast("dict[str, object]", build_markdown_envelope(tmp_path))
        )[0]
        assert fact["parsed"] is None, fact
        assert fact["error"] is not None, fact
        assert fragment in fact["error"], fact
