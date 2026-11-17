"""Specify the workflow facts the pull-request cancellation policy reads."""

from __future__ import annotations

import pathlib
import typing as typ

import pygit2
import pytest

from concordat.errors import OperationalRuleError
from concordat.rules import pr_run_cancellation_envelope
from concordat.rules.codescene_coverage_envelope import (
    build_codescene_coverage_envelope,
)
from concordat.rules.pr_run_cancellation_envelope import (
    ENVELOPE_KIND,
    ENVELOPE_SCHEMA_VERSION,
    OPERATION_READ_WORKFLOW,
    PrRunCancellationEnvelope,
    WorkflowFile,
    build_pr_run_cancellation_envelope,
)


def _workflows(envelope: PrRunCancellationEnvelope) -> list[WorkflowFile]:
    """Return the workflow facts from one policy envelope."""
    return envelope["workflows"]


def _workflow_dir(checkout: pathlib.Path) -> pathlib.Path:
    """Create and return the checkout's workflow directory."""
    directory = checkout / ".github" / "workflows"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _write_workflow(
    checkout: pathlib.Path,
    name: str,
    text: str = "on: pull_request\n",
) -> pathlib.Path:
    """Write one workflow document and return its path."""
    path = _workflow_dir(checkout) / name
    path.write_text(text, encoding="utf-8")
    return path


def test_the_envelope_names_itself_and_its_schema(tmp_path: pathlib.Path) -> None:
    """The policy refuses a document whose kind or version it does not know."""
    envelope = build_pr_run_cancellation_envelope(tmp_path)

    assert envelope["kind"] == ENVELOPE_KIND, envelope
    assert envelope["schema_version"] == ENVELOPE_SCHEMA_VERSION, envelope


def test_a_checkout_with_no_repository_has_no_slug(tmp_path: pathlib.Path) -> None:
    """A local directory has no origin, so no exemption can name it."""
    envelope = build_pr_run_cancellation_envelope(tmp_path)

    assert envelope["repository"] == {"name": None}, envelope


def test_the_origin_slug_names_the_repository(tmp_path: pathlib.Path) -> None:
    """Exemptions are matched on `owner/name`, so the slug must survive."""
    pygit2.init_repository(str(tmp_path))
    repository = pygit2.Repository(str(tmp_path / ".git"))
    repository.remotes.create("origin", "git@github.com:leynos/whitaker.git")

    envelope = build_pr_run_cancellation_envelope(tmp_path)

    assert envelope["repository"] == {"name": "leynos/whitaker"}, envelope


def test_decodes_both_yaml_extensions_in_filename_order(
    tmp_path: pathlib.Path,
) -> None:
    """Include each workflow as decoded YAML evidence for the policy."""
    workflow = "name: test\non: pull_request\njobs: {}\n"
    _write_workflow(tmp_path, "release.yaml", workflow)
    _write_workflow(tmp_path, "ci.yml", workflow)
    _write_workflow(tmp_path, "README.md", "not YAML\n")

    envelope = build_pr_run_cancellation_envelope(tmp_path)

    assert [fact["path"] for fact in _workflows(envelope)] == [
        ".github/workflows/ci.yml",
        ".github/workflows/release.yaml",
    ], envelope
    assert all(fact["error"] is None for fact in _workflows(envelope)), envelope


def test_decodes_each_workflow_to_its_exact_document(tmp_path: pathlib.Path) -> None:
    """Hand the policy the decoded document, not a summary of it."""
    _write_workflow(
        tmp_path,
        "ci.yml",
        "on:\n  pull_request:\nconcurrency:\n  group: ci-${{ github.ref }}\n"
        "  cancel-in-progress: true\njobs:\n  test:\n    runs-on: ubuntu-latest\n",
    )

    envelope = build_pr_run_cancellation_envelope(tmp_path)

    assert _workflows(envelope) == [
        {
            "path": ".github/workflows/ci.yml",
            "parsed": {
                "on": {"pull_request": None},
                "concurrency": {
                    "group": "ci-${{ github.ref }}",
                    "cancel-in-progress": True,
                },
                "jobs": {"test": {"runs-on": "ubuntu-latest"}},
            },
            "error": None,
        }
    ], envelope


def test_carries_malformed_workflow_as_indeterminate_evidence(
    tmp_path: pathlib.Path,
) -> None:
    """Preserve a decode error instead of dropping an unreadable workflow."""
    _write_workflow(tmp_path, "ci.yml", "on: [pull_request\n")

    envelope = build_pr_run_cancellation_envelope(tmp_path)

    fact = _workflows(envelope)[0]
    assert fact["parsed"] is None, fact
    assert "invalid YAML" in str(fact["error"]), fact


def test_rejects_symlinked_workflow_without_reading_target(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Record a symlink as an error without following its target."""
    target = tmp_path / "workflow-target.yml"
    target.write_text("secret: do-not-leak\n", encoding="utf-8")
    (_workflow_dir(tmp_path) / "ci.yml").symlink_to(target)

    read_paths: list[pathlib.Path] = []

    def record_read(path: pathlib.Path) -> str:
        """Record the requested path, then fail so no target is read."""
        read_paths.append(path)
        raise AssertionError

    monkeypatch.setattr(pr_run_cancellation_envelope, "_read_text", record_read)

    envelope = build_pr_run_cancellation_envelope(tmp_path)

    fact = _workflows(envelope)[0]
    assert read_paths == [], read_paths
    assert fact["parsed"] is None, fact
    assert fact["error"] == "workflow file is a symlink", fact
    assert "do-not-leak" not in repr(envelope), envelope


def test_carries_json_conversion_type_error_as_error(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Preserve a JSON type failure as an indeterminate workflow fact."""
    _write_workflow(tmp_path, "ci.yml", "on: pull_request\n")
    monkeypatch.setattr(
        pr_run_cancellation_envelope,
        "_json_safe",
        lambda _: (_ for _ in ()).throw(TypeError("unsupported value")),
    )

    envelope = build_pr_run_cancellation_envelope(tmp_path)

    fact = _workflows(envelope)[0]
    assert fact["parsed"] is None, fact
    assert fact["error"] == ("workflow document is not JSON-safe: unsupported value"), (
        fact
    )


def test_absent_workflow_directory_yields_no_workflows(
    tmp_path: pathlib.Path,
) -> None:
    """Treat a repository with no workflow directory as having none."""
    envelope = build_pr_run_cancellation_envelope(tmp_path)

    assert _workflows(envelope) == [], envelope


def test_an_unreadable_workflow_directory_is_an_operational_error(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refuse the audit rather than report a repository it could not see."""
    workflows = _workflow_dir(tmp_path)

    def refuse(*_args: object, **_kwargs: object) -> typ.NoReturn:
        """Raise the permission error the reader must surface."""
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(pathlib.Path, "iterdir", refuse)

    with pytest.raises(OperationalRuleError) as excinfo:
        build_pr_run_cancellation_envelope(tmp_path)

    assert excinfo.value.operation == OPERATION_READ_WORKFLOW
    assert excinfo.value.resource == workflows
    assert "cannot list" in str(excinfo.value), str(excinfo.value)


def test_both_workflow_consumers_read_the_same_facts(
    tmp_path: pathlib.Path,
) -> None:
    """The extraction is shared, so two rules cannot drift on workflow facts."""
    _write_workflow(
        tmp_path,
        "ci.yml",
        "on: pull_request\nconcurrency:\n  group: ci\n  cancel-in-progress: true\n",
    )
    _write_workflow(tmp_path, "release.yml", "on: [pull_request\n")

    cancellation = build_pr_run_cancellation_envelope(tmp_path)
    coverage = build_codescene_coverage_envelope(tmp_path)

    assert cancellation["workflows"] == coverage["workflows"], cancellation
    assert [fact["path"] for fact in cancellation["workflows"]] == [
        ".github/workflows/ci.yml",
        ".github/workflows/release.yml",
    ], cancellation


def test_a_workflow_directory_outside_the_checkout_is_refused(
    tmp_path: pathlib.Path,
) -> None:
    """Refuse a linked directory instead of auditing another tree's workflows."""
    outside = tmp_path / "outside" / "workflows"
    outside.mkdir(parents=True)
    (outside / "ci.yml").write_text("on: pull_request\n", encoding="utf-8")
    checkout = tmp_path / "checkout"
    (checkout / ".github").mkdir(parents=True)
    (checkout / ".github" / "workflows").symlink_to(outside)

    with pytest.raises(OperationalRuleError) as excinfo:
        build_pr_run_cancellation_envelope(checkout)

    assert excinfo.value.operation == OPERATION_READ_WORKFLOW
    assert excinfo.value.resource == checkout / ".github" / "workflows"
    assert "resolves outside the checkout" in str(excinfo.value)
