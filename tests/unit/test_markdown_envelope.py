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
    _exists,
    _has_markdown_files,
    _is_dir,
    _is_file,
    _is_symlink,
    build_markdown_envelope,
)
from tests.unit.rule_test_support import MINIMAL_REPORT

if typ.TYPE_CHECKING:
    import collections.abc as cabc

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


class TestSymlinkedPolicyInputs:
    """A policy input may not be read from outside the audited checkout.

    Every reader follows symbolic links. Without a containment guard a
    checkout could aim its `Makefile`, its markdownlint configuration, or a
    workflow file at any readable file on the machine, and that file's
    contents would enter the envelope the audit reports and may publish.
    """

    @staticmethod
    def _outside(tmp_path: pathlib.Path, name: str, text: str) -> pathlib.Path:
        """Create *name* outside the checkout and return its path."""
        outside = tmp_path / "outside"
        outside.mkdir(exist_ok=True)
        target = outside / name
        target.write_text(text, encoding="utf-8")
        return target

    @staticmethod
    def _checkout(tmp_path: pathlib.Path) -> pathlib.Path:
        """Return an applicable checkout directory."""
        checkout = tmp_path / "checkout"
        checkout.mkdir(exist_ok=True)
        (checkout / "README.md").write_text("# Hi\n", encoding="utf-8")
        return checkout

    def test_symlinked_markdownlint_config_is_refused(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A configuration linked outside the checkout is not read."""
        checkout = self._checkout(tmp_path)
        target = self._outside(tmp_path, "secrets.jsonc", '{"config": {}}')
        (checkout / ".markdownlint-cli2.jsonc").symlink_to(target)
        with pytest.raises(OperationalRuleError, match="outside the checkout"):
            build_markdown_envelope(checkout)

    def test_symlinked_makefile_is_refused(self, tmp_path: pathlib.Path) -> None:
        """A `Makefile` linked outside the checkout is never parsed."""
        checkout = self._checkout(tmp_path)
        target = self._outside(tmp_path, "Makefile", "fmt:\n\techo hi\n")
        (checkout / "Makefile").symlink_to(target)
        with pytest.raises(OperationalRuleError, match="outside the checkout"):
            build_markdown_envelope(checkout)

    def test_symlinked_workflows_directory_is_refused(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A workflows directory linked outside the checkout is not listed."""
        checkout = self._checkout(tmp_path)
        outside = tmp_path / "outside" / "workflows"
        outside.mkdir(parents=True)
        (outside / "ci.yml").write_text("on: push\n", encoding="utf-8")
        (checkout / ".github").mkdir()
        (checkout / ".github" / "workflows").symlink_to(outside)
        with pytest.raises(OperationalRuleError, match="outside the checkout"):
            build_markdown_envelope(checkout)

    def test_symlinked_workflow_file_is_refused(self, tmp_path: pathlib.Path) -> None:
        """One workflow file linked outside the checkout is not decoded."""
        checkout = self._checkout(tmp_path)
        target = self._outside(tmp_path, "elsewhere.yml", "on: push\n")
        workflows = checkout / ".github" / "workflows"
        workflows.mkdir(parents=True)
        (workflows / "ci.yml").symlink_to(target)
        with pytest.raises(OperationalRuleError, match="outside the checkout"):
            build_markdown_envelope(checkout)

    def test_a_link_inside_the_checkout_is_read(self, tmp_path: pathlib.Path) -> None:
        """Containment is the test, not the link: an internal link is fine."""
        checkout = self._checkout(tmp_path)
        real = checkout / "config" / ".markdownlint-cli2.jsonc"
        real.parent.mkdir()
        real.write_text('{"config": {"MD004": {"style": "dash"}}}', encoding="utf-8")
        (checkout / ".markdownlint-cli2.jsonc").symlink_to(real)
        envelope = build_markdown_envelope(checkout)
        config = _config(typ.cast("dict[str, object]", envelope))
        assert config["error"] is None, config
        assert config["parsed"] == {"config": {"MD004": {"style": "dash"}}}, config


class TestUnlistableDirectories:
    """A directory the scan cannot read is an audit failure, not an absence."""

    def test_inaccessible_parent_directory_is_operational(
        self, tmp_path: pathlib.Path
    ) -> None:
        """An unreadable parent makes a policy input inaccessible, not absent.

        From Python 3.14 the `pathlib` predicates suppress every `OSError`,
        so `.github/workflows` under an unreadable `.github` answers "not a
        directory" exactly as a repository without workflows does. PD-006
        would then report that CI does not lint Markdown for a checkout the
        audit was never able to read.
        """
        checkout = tmp_path / "checkout"
        checkout.mkdir()
        (checkout / "README.md").write_text("# Hi\n", encoding="utf-8")
        github = checkout / ".github"
        (github / "workflows").mkdir(parents=True)
        (github / "workflows" / "ci.yml").write_text("on: push\n", encoding="utf-8")
        github.chmod(0o000)
        try:
            with pytest.raises(OperationalRuleError, match="cannot examine"):
                build_markdown_envelope(checkout)
        finally:
            github.chmod(0o755)

    def test_a_genuinely_absent_input_is_not_an_error(
        self, tmp_path: pathlib.Path
    ) -> None:
        """The other half: a missing path is still recorded as missing.

        A guard that refused every unreadable path would be satisfied by
        refusing every path; this pins that an ordinary checkout without a
        `Makefile`, configuration, or workflows still builds an envelope.
        """
        checkout = tmp_path / "checkout"
        checkout.mkdir()
        (checkout / "README.md").write_text("# Hi\n", encoding="utf-8")
        envelope = build_markdown_envelope(checkout)
        applicability = typ.cast(
            "dict[str, bool]",
            typ.cast("dict[str, object]", envelope)["applicability"],
        )
        assert applicability["markdown_files"] is True, envelope
        assert applicability["root_makefile"] is False, envelope
        assert applicability["markdownlint_config"] is False, envelope
        assert applicability["workflows_dir"] is False, envelope

    def test_unlistable_workflows_directory_is_operational(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A workflows directory that exists but cannot be listed raises.

        Returning an empty list would record a checkout with no workflows,
        and PD-006 would then report that CI does not lint Markdown for a
        repository whose workflows the audit never managed to read.
        """
        checkout = tmp_path / "checkout"
        workflows = checkout / ".github" / "workflows"
        workflows.mkdir(parents=True)
        (workflows / "ci.yml").write_text("on: push\n", encoding="utf-8")
        (checkout / "README.md").write_text("# Hi\n", encoding="utf-8")
        workflows.chmod(0o000)
        try:
            with pytest.raises(OperationalRuleError, match="cannot list"):
                build_markdown_envelope(checkout)
        finally:
            workflows.chmod(0o755)

    def test_unlistable_directory_stops_the_scan(self, tmp_path: pathlib.Path) -> None:
        """`os.walk` swallows errors; the builder must not inherit that.

        A checkout whose subdirectory cannot be listed would otherwise
        contribute no Markdown files, and the rule would report
        `not-applicable` for a repository it never managed to look at.
        """
        checkout = tmp_path / "checkout"
        nested = checkout / "docs"
        nested.mkdir(parents=True)
        (nested / "guide.md").write_text("# Guide\n", encoding="utf-8")
        nested.chmod(0o000)
        try:
            with pytest.raises(OperationalRuleError, match="cannot scan"):
                _has_markdown_files(checkout)
        finally:
            nested.chmod(0o755)


class TestPathProbes:
    """Each probe distinguishes "not there" from "could not look".

    The envelope's callers reach these through paths a containment guard has
    usually already touched, so the probes are driven here directly: a test
    that exercises them only through `build_markdown_envelope` passes on
    whichever guard happens to raise first and proves nothing about the
    probe it names.
    """

    @staticmethod
    def _unreadable(tmp_path: pathlib.Path) -> pathlib.Path:
        """Return a path inside a directory that cannot be read."""
        parent = tmp_path / "locked"
        parent.mkdir()
        (parent / "target").write_text("x", encoding="utf-8")
        parent.chmod(0o000)
        return parent / "target"

    @pytest.mark.parametrize(
        "probe",
        [
            pytest.param(_exists, id="exists"),
            pytest.param(_is_file, id="is_file"),
            pytest.param(_is_dir, id="is_dir"),
            pytest.param(_is_symlink, id="is_symlink"),
        ],
    )
    def test_an_unreadable_parent_raises(
        self,
        tmp_path: pathlib.Path,
        probe: cabc.Callable[[pathlib.Path, str], bool],
    ) -> None:
        """From Python 3.14 `pathlib` answers False here; the audit must not.

        `Path.exists`, `Path.is_file`, `Path.is_dir`, and `Path.is_symlink`
        suppress every `OSError` on 3.14, so an unreadable parent makes a
        policy input indistinguishable from one that was never there.
        """
        target = self._unreadable(tmp_path)
        try:
            with pytest.raises(OperationalRuleError, match="cannot examine"):
                probe(target, "probe-path")
        finally:
            target.parent.chmod(0o755)

    @pytest.mark.parametrize(
        ("probe", "expected"),
        [
            pytest.param(_exists, True, id="exists-file"),
            pytest.param(_is_file, True, id="is-a-file"),
            pytest.param(_is_dir, False, id="not-a-directory"),
            pytest.param(_is_symlink, False, id="not-a-link"),
        ],
    )
    def test_a_readable_file_answers_normally(
        self,
        tmp_path: pathlib.Path,
        probe: cabc.Callable[[pathlib.Path, str], bool],
        *,
        expected: bool,
    ) -> None:
        """The narrow half: an ordinary file still gets an ordinary answer."""
        target = tmp_path / "plain.txt"
        target.write_text("x", encoding="utf-8")
        assert probe(target, "probe-path") is expected, (
            "a probe misread an ordinary readable file"
        )

    @pytest.mark.parametrize(
        "probe",
        [
            pytest.param(_exists, id="exists"),
            pytest.param(_is_file, id="is_file"),
            pytest.param(_is_dir, id="is_dir"),
            pytest.param(_is_symlink, id="is_symlink"),
        ],
    )
    def test_a_missing_path_is_false_not_an_error(
        self,
        tmp_path: pathlib.Path,
        probe: cabc.Callable[[pathlib.Path, str], bool],
    ) -> None:
        """A path that genuinely is not there is absent, not inaccessible."""
        assert probe(tmp_path / "nowhere", "probe-path") is False, (
            "a probe treated a missing path as present or as a refusal"
        )

    @pytest.mark.parametrize(
        "probe",
        [
            pytest.param(_exists, id="exists"),
            pytest.param(_is_file, id="is_file"),
            pytest.param(_is_dir, id="is_dir"),
        ],
    )
    def test_a_dangling_link_is_a_refusal_not_an_absence(
        self,
        tmp_path: pathlib.Path,
        probe: cabc.Callable[[pathlib.Path, str], bool],
    ) -> None:
        """`stat` cannot tell a missing entry from a link with no target.

        Both raise `FileNotFoundError`. They are different answers: nothing
        was ever there, against something is there and points at nothing.
        Reporting a dangling policy input as absent omits a fact the audit
        was meant to read and sends the caller down the absence branch.
        """
        link = tmp_path / "dangling.yaml"
        link.symlink_to(tmp_path / "never-existed.yaml")
        with pytest.raises(OperationalRuleError, match="cannot examine"):
            probe(link, "probe-path")

    def test_a_dangling_link_is_still_a_link(self, tmp_path: pathlib.Path) -> None:
        """`_is_symlink` reads the link, so a missing target does not hide it."""
        link = tmp_path / "dangling.md"
        link.symlink_to(tmp_path / "never-existed.md")
        assert _is_symlink(link, "probe-path") is True, (
            "a link with no target is still a link"
        )

    def test_a_dangling_markdown_link_does_not_stop_the_scan(
        self, tmp_path: pathlib.Path
    ) -> None:
        """The Markdown walk skips a dangling link as it skips any link.

        The walk is over whatever the tree happens to hold, not over the
        policy inputs the audit must read, so a broken link in it is not a
        reason to refuse the whole checkout.
        """
        (tmp_path / "broken.md").symlink_to(tmp_path / "never-existed.md")
        assert _has_markdown_files(tmp_path) is False, (
            "a dangling link is not a governed Markdown file"
        )
        (tmp_path / "real.md").write_text("# Hi\n", encoding="utf-8")
        assert _has_markdown_files(tmp_path) is True, (
            "a real document beside the dangling link still counts"
        )

    def test_a_symlink_is_judged_without_following_it(
        self, tmp_path: pathlib.Path
    ) -> None:
        """`_is_symlink` reads the link itself, `_is_file` its target."""
        target = tmp_path / "real.md"
        target.write_text("# Hi\n", encoding="utf-8")
        link = tmp_path / "link.md"
        link.symlink_to(target)
        assert _is_symlink(link, "probe-path") is True, "the link is a link"
        assert _is_file(link, "probe-path") is True, (
            "the link resolves to a regular file"
        )
        assert _is_symlink(target, "probe-path") is False, (
            "the target is not itself a link"
        )
