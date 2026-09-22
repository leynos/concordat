"""Specify the decoded workflow facts for CodeScene coverage policy checks."""

from __future__ import annotations

import dataclasses
import pathlib
import typing as typ

import pytest

from concordat.errors import OperationalRuleError
from concordat.rules import codescene_coverage_envelope
from concordat.rules.codescene_coverage_envelope import (
    ENVELOPE_KIND,
    ENVELOPE_SCHEMA_VERSION,
    OPERATION_READ_WORKFLOW,
    CoverageEnvelope,
    WorkflowFile,
    build_codescene_coverage_envelope,
)

if typ.TYPE_CHECKING:
    import collections.abc as cabc


def _workflows(envelope: CoverageEnvelope) -> list[WorkflowFile]:
    """Return the workflow facts from one policy envelope."""
    return envelope["workflows"]


def _workflow_dir(checkout: pathlib.Path) -> pathlib.Path:
    """Create and return the checkout's workflow directory."""
    directory = checkout / ".github" / "workflows"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


@dataclasses.dataclass(frozen=True, slots=True)
class _DenialCase:
    """One filesystem denial and the error the reader must raise for it.

    Attributes
    ----------
    method:
        `pathlib.Path` method that refuses.
    resource:
        Whether the error must name the workflow directory or the file.
    detail:
        Fragment the error message must carry.

    """

    method: str
    resource: str
    detail: str


def _write_workflow(
    checkout: pathlib.Path, name: str, text: str = "on: pull_request\n"
) -> pathlib.Path:
    """Write one workflow document and return its path."""
    path = _workflow_dir(checkout) / name
    path.write_text(text, encoding="utf-8")
    return path


class TestBuildCodesceneCoverageEnvelope:
    """Record every local workflow without interpreting GitHub Actions syntax."""

    def test_decodes_both_yaml_extensions_in_filename_order(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Include each workflow as decoded YAML evidence for the policy."""
        workflow = "name: test\non: pull_request\njobs: {}\n"
        _write_workflow(tmp_path, "coverage.yaml", workflow)
        _write_workflow(tmp_path, "ci.yml", workflow)
        _write_workflow(tmp_path, "README.md", "not YAML\n")

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
        _write_workflow(tmp_path, "ci.yml", "on: [pull_request\n")

        envelope = build_codescene_coverage_envelope(tmp_path)

        fact = _workflows(envelope)[0]
        assert fact["parsed"] is None, fact
        assert "invalid YAML" in str(fact["error"]), fact

    def test_rejects_symlinked_workflow_without_reading_target(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Record a symlink as an error without following its target."""
        target = tmp_path / "workflow-target.yml"
        target.write_text("secret: do-not-leak\n", encoding="utf-8")
        (_workflow_dir(tmp_path) / "ci.yml").symlink_to(target)

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
        _write_workflow(
            tmp_path, "ci.yml", "on: pull_request\nloop: &loop\n  self: *loop\n"
        )

        envelope = build_codescene_coverage_envelope(tmp_path)

        fact = _workflows(envelope)[0]
        assert fact["parsed"] is None, fact
        assert str(fact["error"]).startswith("workflow document is not JSON-safe:"), (
            fact
        )
        assert "Circular reference detected" in str(fact["error"]), fact

    @pytest.mark.parametrize(
        "scalar",
        [
            pytest.param(".nan", id="not-a-number"),
            pytest.param(".inf", id="infinity"),
            pytest.param("-.inf", id="negative-infinity"),
        ],
    )
    def test_carries_a_non_finite_number_as_a_content_error(
        self, tmp_path: pathlib.Path, scalar: str
    ) -> None:
        """Keep a non-finite number as one file's error, not the run's.

        Python's JSON encoder emits `NaN` and `Infinity`, which no parser
        accepts, so recording the document as decoded would fail the whole
        evaluation when the policy engine refused it.
        """
        _write_workflow(tmp_path, "ci.yml", f"on: pull_request\ntimeout: {scalar}\n")

        envelope = build_codescene_coverage_envelope(tmp_path)

        fact = _workflows(envelope)[0]
        assert fact["parsed"] is None, fact
        assert str(fact["error"]).startswith("workflow document is not JSON-safe:"), (
            fact
        )

    def test_carries_json_conversion_type_error_as_error(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Preserve a JSON type failure as an indeterminate workflow fact."""
        _write_workflow(tmp_path, "ci.yml", "on: pull_request\n")
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

    def test_carries_the_schema_version_kind_and_repository_identity(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Name the envelope contract the policy's `envelope_ok` guard reads."""
        _write_workflow(tmp_path, "ci.yml", "on: pull_request\njobs: {}\n")

        envelope = build_codescene_coverage_envelope(tmp_path)

        assert envelope["schema_version"] == ENVELOPE_SCHEMA_VERSION, envelope
        assert envelope["kind"] == ENVELOPE_KIND, envelope
        assert envelope["repository"] == {"path": str(tmp_path), "name": None}, envelope

    def test_decodes_each_workflow_to_its_exact_document(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Hand the policy the decoded document, not a summary of it."""
        _write_workflow(
            tmp_path,
            "ci.yml",
            "on:\n  pull_request:\njobs:\n  coverage:\n    runs-on: ubuntu-latest\n"
            "    steps:\n      - uses: generate-coverage\n"
            "        with:\n          with-ratchet: true\n",
        )

        envelope = build_codescene_coverage_envelope(tmp_path)

        assert _workflows(envelope) == [
            {
                "path": ".github/workflows/ci.yml",
                "parsed": {
                    "on": {"pull_request": None},
                    "jobs": {
                        "coverage": {
                            "runs-on": "ubuntu-latest",
                            "steps": [
                                {
                                    "uses": "generate-coverage",
                                    "with": {"with-ratchet": True},
                                }
                            ],
                        }
                    },
                },
                "error": None,
            }
        ], envelope

    def test_absent_workflow_directory_yields_no_workflows(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Treat a repository with no workflow directory as having none."""
        envelope = build_codescene_coverage_envelope(tmp_path)

        assert _workflows(envelope) == [], envelope

    def test_empty_workflow_directory_yields_no_workflows(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Distinguish an empty directory from an unreadable one."""
        _workflow_dir(tmp_path)

        envelope = build_codescene_coverage_envelope(tmp_path)

        assert _workflows(envelope) == [], envelope

    def test_workflow_path_that_is_a_file_is_an_operational_error(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Refuse to read a checkout whose workflow directory is a file."""
        github = tmp_path / ".github"
        github.mkdir()
        (github / "workflows").write_text("not a directory\n", encoding="utf-8")

        with pytest.raises(OperationalRuleError) as excinfo:
            build_codescene_coverage_envelope(tmp_path)

        assert excinfo.value.operation == OPERATION_READ_WORKFLOW
        assert excinfo.value.resource == tmp_path / ".github" / "workflows"
        assert "cannot list" in str(excinfo.value)

    @pytest.mark.parametrize(
        "case",
        [
            pytest.param(
                _DenialCase("iterdir", "directory", "cannot list"),
                id="directory-enumeration",
            ),
            pytest.param(
                _DenialCase("lstat", "file", "cannot stat"), id="entry-status"
            ),
            pytest.param(
                _DenialCase("read_text", "file", "cannot read"), id="file-contents"
            ),
        ],
    )
    def test_an_unreadable_workflow_path_is_an_operational_error(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
        case: _DenialCase,
    ) -> None:
        """Refuse the audit at each point the filesystem can deny the reader.

        Every one of these is a shape the reader cannot see. Answering "no
        workflows" for any of them would report the repository as being in
        perfect order and clear every clause of the rule at once, so each
        raises instead, naming the path that failed.
        """
        workflow = _write_workflow(tmp_path, "ci.yml")
        expected = workflow.parent if case.resource == "directory" else workflow

        def refuse(*_args: object, **_kwargs: object) -> typ.NoReturn:
            raise PermissionError(13, "Permission denied")

        monkeypatch.setattr(pathlib.Path, case.method, refuse)

        with pytest.raises(OperationalRuleError) as excinfo:
            build_codescene_coverage_envelope(tmp_path)

        assert excinfo.value.operation == OPERATION_READ_WORKFLOW
        assert excinfo.value.resource == expected
        assert case.detail in str(excinfo.value)

    def test_a_dangling_workflow_directory_link_is_not_absence(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Refuse a broken link rather than reading it as no workflows.

        A symlink whose target is gone raises the same missing-file error as
        no path at all, so treating that error as absence would report a
        repository whose workflow directory is broken as having none.
        """
        checkout = tmp_path / "checkout"
        (checkout / ".github").mkdir(parents=True)
        (checkout / ".github" / "workflows").symlink_to(checkout / "gone")

        with pytest.raises(OperationalRuleError) as excinfo:
            build_codescene_coverage_envelope(checkout)

        assert excinfo.value.operation == OPERATION_READ_WORKFLOW
        assert excinfo.value.resource == checkout / ".github" / "workflows"

    def test_a_dangling_ancestor_is_not_absence(self, tmp_path: pathlib.Path) -> None:
        """Refuse a workflow directory under a link that does not resolve.

        `lstat` does not follow the final component, but it resolves every
        component above it, so a dangling `.github` makes `.github/workflows`
        raise the same error a checkout with no workflows does. Reading that
        as absence would turn a broken checkout into a compliant one.
        """
        checkout = tmp_path / "checkout"
        checkout.mkdir()
        (checkout / ".github").symlink_to(checkout / "absent-github")

        with pytest.raises(OperationalRuleError) as excinfo:
            build_codescene_coverage_envelope(checkout)

        assert excinfo.value.operation == OPERATION_READ_WORKFLOW
        assert excinfo.value.resource == checkout / ".github" / "workflows"
        assert "does not resolve" in str(excinfo.value), str(excinfo.value)

    def test_a_resolved_ancestor_leaves_an_absent_directory_absent(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A checkout whose `.github` is a real directory has no workflows."""
        checkout = tmp_path / "checkout"
        (checkout / ".github").mkdir(parents=True)

        envelope = build_codescene_coverage_envelope(checkout)

        assert _workflows(envelope) == [], envelope

    def test_an_undescribable_workflow_directory_reports_its_own_reason(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Report why the entry could not be described, not the read's error.

        A refusal to describe the entry is neither presence nor absence. The
        error the caller sees must name it, rather than the missing-file
        error that sent the reader to the entry in the first place.
        """
        checkout = tmp_path / "checkout"
        (checkout / ".github").mkdir(parents=True)
        workflows = checkout / ".github" / "workflows"

        def missing(_self: pathlib.Path) -> cabc.Iterator[pathlib.Path]:
            raise FileNotFoundError(2, "No such file or directory")

        def refuse(_self: pathlib.Path) -> object:
            raise PermissionError(13, "Permission denied")

        monkeypatch.setattr(pathlib.Path, "iterdir", missing)
        monkeypatch.setattr(pathlib.Path, "lstat", refuse)

        with pytest.raises(OperationalRuleError) as excinfo:
            build_codescene_coverage_envelope(checkout)

        assert excinfo.value.resource == workflows, excinfo.value.resource
        assert "Permission denied" in str(excinfo.value), str(excinfo.value)

    @pytest.mark.parametrize(
        "link_name",
        [
            pytest.param(".github", id="github-directory"),
            pytest.param(".github/workflows", id="workflow-directory"),
        ],
    )
    def test_a_workflow_directory_outside_the_checkout_is_refused(
        self, tmp_path: pathlib.Path, link_name: str
    ) -> None:
        """Refuse a linked workflow directory instead of auditing its target.

        The audit reports on the checkout it was given. Reading a directory
        that resolves elsewhere would let a link in the repository decide the
        verdict, and would record another tree's workflows as this one's.
        """
        outside = tmp_path / "outside" / "workflows"
        outside.mkdir(parents=True)
        (outside / "ci.yml").write_text("on: pull_request\n", encoding="utf-8")
        checkout = tmp_path / "checkout"
        link = checkout / link_name
        link.parent.mkdir(parents=True)
        link.symlink_to(outside if link_name.endswith("workflows") else outside.parent)

        with pytest.raises(OperationalRuleError) as excinfo:
            build_codescene_coverage_envelope(checkout)

        assert excinfo.value.operation == OPERATION_READ_WORKFLOW
        assert excinfo.value.resource == checkout / ".github" / "workflows"
        assert "resolves outside the checkout" in str(excinfo.value)

    def test_a_workflow_directory_inside_the_checkout_is_read(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A link that stays inside the checkout is still the checkout's own."""
        checkout = tmp_path / "checkout"
        real = checkout / "workflows-source"
        real.mkdir(parents=True)
        (real / "ci.yml").write_text("on: pull_request\n", encoding="utf-8")
        (checkout / ".github").mkdir()
        (checkout / ".github" / "workflows").symlink_to(real)

        envelope = build_codescene_coverage_envelope(checkout)

        assert [fact["path"] for fact in _workflows(envelope)] == [
            ".github/workflows/ci.yml"
        ], envelope

    def test_records_invalid_utf8_as_a_content_error(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Keep an undecodable workflow as evidence the policy can act on."""
        (_workflow_dir(tmp_path) / "ci.yml").write_bytes(
            b"on: pull_request\n\xff\xfe\n"
        )

        envelope = build_codescene_coverage_envelope(tmp_path)

        fact = _workflows(envelope)[0]
        assert fact["parsed"] is None, fact
        assert str(fact["error"]).startswith("not UTF-8 text:"), fact

    def test_records_a_non_mapping_document_as_a_content_error(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Refuse to evaluate a workflow whose document is not a mapping."""
        _write_workflow(tmp_path, "ci.yml", "- pull_request\n")

        envelope = build_codescene_coverage_envelope(tmp_path)

        fact = _workflows(envelope)[0]
        assert fact["parsed"] is None, fact
        assert fact["error"] == "workflow document is not a mapping", fact

    def test_records_an_empty_document_as_a_content_error(
        self, tmp_path: pathlib.Path
    ) -> None:
        """An empty workflow decodes to None, which is not a mapping either."""
        _write_workflow(tmp_path, "ci.yml", "")

        envelope = build_codescene_coverage_envelope(tmp_path)

        fact = _workflows(envelope)[0]
        assert fact["error"] == "workflow document is not a mapping", fact

    def test_reads_only_the_workflow_directory_root(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Ignore nested directories and non-workflow suffixes.

        GitHub reads workflows from the directory root alone, so a nested
        `*.yml` is not a workflow and a directory named `*.yml` is not a file.
        """
        workflows = _workflow_dir(tmp_path)
        (workflows / "nested").mkdir()
        (workflows / "nested" / "inner.yml").write_text("on: push\n", encoding="utf-8")
        (workflows / "directory.yml").mkdir()
        _write_workflow(tmp_path, "notes.txt", "on: push\n")
        _write_workflow(tmp_path, "ci.yml")

        envelope = build_codescene_coverage_envelope(tmp_path)

        assert [fact["path"] for fact in _workflows(envelope)] == [
            ".github/workflows/ci.yml"
        ], envelope
