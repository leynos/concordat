"""Unit tests for the shared GitHub Actions workflow collector.

The collector is imported by more than one canon rule package, so the way it
reports a document it cannot decode is part of the contract those packages
build on: a bad file becomes that file's indeterminate evidence, never an
abort of the whole audit.
"""

from __future__ import annotations

import os
import pathlib
import typing as typ

import pytest

from concordat.errors import OperationalRuleError
from concordat.rules import workflow_evidence
from concordat.rules.workflow_evidence import load_workflows, read_text


def test_a_symlink_is_refused_when_the_workflow_is_opened(
    tmp_path: pathlib.Path,
) -> None:
    """A link swapped in after the mode check is not followed.

    The collector rejects a link it saw while listing the directory, but a
    checkout another process can write to may change between that check and
    the read. `O_NOFOLLOW` keeps the second case from reading another file's
    content as this repository's workflow.
    """
    target = tmp_path / "outside.yml"
    target.write_text("on: push\n", encoding="utf-8")
    link = tmp_path / "ci.yml"
    link.symlink_to(target)
    with pytest.raises(OperationalRuleError):
        read_text(link)


def test_a_fifo_replaced_before_the_read_is_refused(
    tmp_path: pathlib.Path,
) -> None:
    """A FIFO that replaced the entry is rejected before it is read.

    `O_NOFOLLOW` refuses a link but not a FIFO, and a read-only open of a
    FIFO waits for a writer, which would stall the audit indefinitely. The
    open is non-blocking and the descriptor's type is checked before any
    read, so the replacement becomes this file's operational failure.
    """
    fifo = tmp_path / "ci.yml"
    os.mkfifo(fifo)
    with pytest.raises(OperationalRuleError):
        read_text(fifo)


def test_a_root_entry_replaced_by_a_link_after_classification_is_refused(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The collector's own read refuses a link swapped in after classification.

    The cancellation and CodeScene builders never call `read_text`; they list,
    classify and read through `load_workflows`. A regression that dropped
    `O_NOFOLLOW` from `_read_entry` would therefore go unnoticed by a test
    that exercises `read_text` alone.
    """
    checkout = tmp_path / "repo"
    directory = checkout / ".github" / "workflows"
    directory.mkdir(parents=True)
    workflow = directory / "ci.yml"
    workflow.write_text("on: push\n", encoding="utf-8")
    outside = tmp_path / "outside.yml"
    outside.write_text("name: leaked\n", encoding="utf-8")
    classify = workflow_evidence._workflow_entry_mode

    def classify_then_replace(
        directory_fd: int, name: str, path: pathlib.Path
    ) -> int | None:
        """Replace the workflow with a link after its mode was accepted."""
        mode = classify(directory_fd, name, path)
        if name == "ci.yml":
            workflow.unlink()
            workflow.symlink_to(outside)
        return mode

    monkeypatch.setattr(
        workflow_evidence, "_workflow_entry_mode", classify_then_replace
    )

    with pytest.raises(OperationalRuleError) as excinfo:
        load_workflows(checkout)

    assert "cannot read" in str(excinfo.value), str(excinfo.value)
    assert "leaked" not in str(excinfo.value), str(excinfo.value)


def test_a_root_entry_replaced_by_a_fifo_after_classification_is_refused(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A FIFO swap is refused from the descriptor rather than blocking.

    `O_NONBLOCK` makes the read-only open return immediately, and the
    descriptor type check then refuses it. The suite's global timeout bounds
    the test if either guarantee regresses, so a regression fails the test
    instead of waiting for a writer that never comes.
    """
    checkout = tmp_path / "repo"
    directory = checkout / ".github" / "workflows"
    directory.mkdir(parents=True)
    workflow = directory / "ci.yml"
    workflow.write_text("on: push\n", encoding="utf-8")
    classify = workflow_evidence._workflow_entry_mode

    def classify_then_replace(
        directory_fd: int, name: str, path: pathlib.Path
    ) -> int | None:
        """Replace the workflow with a FIFO after its mode was accepted."""
        mode = classify(directory_fd, name, path)
        if name == "ci.yml":
            workflow.unlink()
            os.mkfifo(workflow)
        return mode

    monkeypatch.setattr(
        workflow_evidence, "_workflow_entry_mode", classify_then_replace
    )

    with pytest.raises(OperationalRuleError) as excinfo:
        load_workflows(checkout)

    assert "not a regular file" in str(excinfo.value), str(excinfo.value)


def test_each_document_is_decoded_by_its_own_loader(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed parse must not decide every later document's verdict.

    ruamel does not guarantee that a `YAML` instance which raised can handle
    further input. Sharing one instance across an audit would make the first
    malformed workflow a property of every file read after it, so the
    collector builds a loader per document.
    """
    directory = tmp_path / ".github" / "workflows"
    directory.mkdir(parents=True)
    (directory / "ci.yml").write_text("on: push\n", encoding="utf-8")
    (directory / "release.yml").write_text("on: push\n", encoding="utf-8")
    loaders: list[object] = []
    yaml_type = workflow_evidence.YAML

    def recording_yaml(*, typ: str | None = None) -> object:
        """Record the loader the collector builds, then delegate to ruamel."""
        loader = yaml_type(typ=typ)
        loaders.append(loader)
        return loader

    monkeypatch.setattr(workflow_evidence, "YAML", recording_yaml)
    facts = load_workflows(tmp_path)
    assert len(facts) == 2, facts
    assert len(loaders) == 2, loaders
    assert loaders[0] is not loaders[1], loaders


@pytest.mark.parametrize(
    ("content", "fragment"),
    [
        # `2026-02-30` fails inside the safe loader's timestamp constructor
        # with a `ValueError` rather than a `YAMLError`.
        pytest.param(
            "on: push\nenv:\n  BUILD_DATE: 2026-02-30\n",
            "invalid YAML",
            id="out-of-range-timestamp",
        ),
        # `!!omap` builds a mapping from its pairs, and a sequence key makes
        # that construction raise `TypeError`.
        pytest.param(
            "on: push\nenv: !!omap\n  - ? [a, b]\n    : value\n",
            "invalid YAML",
            id="omap-sequence-key",
        ),
        # `.nan` decodes to a float JSON cannot encode, which the strict
        # converter refuses rather than emitting `NaN`.
        pytest.param(
            "on: push\nenv:\n  SINCE: .nan\n",
            "not JSON-safe",
            id="not-a-number",
        ),
    ],
)
def test_content_the_loader_cannot_build_is_a_content_error(
    tmp_path: pathlib.Path, content: str, fragment: str
) -> None:
    """Content the reader cannot decode is reported, not raised.

    Each of these shapes fails after the YAML parse or inside it with an
    exception other than `YAMLError`. An uncaught one would abort the whole
    audit instead of carrying this file as indeterminate evidence.
    """
    directory = tmp_path / ".github" / "workflows"
    directory.mkdir(parents=True)
    (directory / "a.yml").write_text(content, encoding="utf-8")
    facts = load_workflows(tmp_path)
    assert len(facts) == 1, facts
    assert facts[0]["parsed"] is None, facts
    assert fragment in (facts[0]["error"] or ""), facts


def test_a_checkout_replaced_after_containment_is_refused(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A root replaced between the containment check and the open is refused.

    Containment compares the resolved workflow directory with the resolved
    checkout, both taken from pathnames. Opening the checkout afterwards can
    land on a different tree, so the walk matches the opened descriptor
    against the identity read before resolution.
    """
    checkout = tmp_path / "repo"
    workflow_directory = checkout / ".github" / "workflows"
    workflow_directory.mkdir(parents=True)
    (workflow_directory / "ci.yml").write_text("on: push\n", encoding="utf-8")
    resolve = workflow_evidence._contained_workflow_directory

    def resolve_then_replace(
        path: pathlib.Path,
    ) -> workflow_evidence._ContainedDirectory:
        """Record containment, then swap a different tree into the same path."""
        location = resolve(path)
        replacement = tmp_path / "replacement"
        replacement_workflows = replacement / ".github" / "workflows"
        replacement_workflows.mkdir(parents=True)
        (replacement_workflows / "ci.yml").write_text("on: push\n", encoding="utf-8")
        checkout.rename(tmp_path / "moved")
        replacement.rename(checkout)
        return location

    monkeypatch.setattr(
        workflow_evidence, "_contained_workflow_directory", resolve_then_replace
    )

    with pytest.raises(OperationalRuleError) as excinfo:
        load_workflows(checkout)

    assert "replaced after its identity was read" in str(excinfo.value), str(
        excinfo.value
    )


def test_a_checkout_replaced_while_its_route_is_resolved_is_refused(
    tmp_path: pathlib.Path,
) -> None:
    """A root swapped while its route resolves is refused, not walked.

    The identity is read before the route is resolved, so a checkout replaced
    between those steps cannot agree with the tree the walk opens. Reading the
    identity afterwards would describe whichever tree resolution reached, and
    the replacement's workflows would then be audited as the named checkout's.
    """
    checkout = tmp_path / "repo"
    workflow_directory = checkout / ".github" / "workflows"
    workflow_directory.mkdir(parents=True)
    (workflow_directory / "ci.yml").write_text("on: push\n", encoding="utf-8")
    replacement = tmp_path / "replacement"
    replacement_workflows = replacement / ".github" / "workflows"
    replacement_workflows.mkdir(parents=True)
    (replacement_workflows / "ci.yml").write_text("on: push\n", encoding="utf-8")
    swapped = False

    class ReplacingPath(pathlib.Path):
        """A path that swaps the checkout when its route is first resolved."""

        # `strict` mirrors `pathlib.Path.resolve`, which accepts it positionally,
        # so the override has to keep the boolean default in that position.
        @typ.override
        def resolve(self, strict: bool = False) -> typ.Self:
            """Swap the checkout in before it is resolved, once."""
            nonlocal swapped
            if not swapped:
                swapped = True
                checkout.rename(tmp_path / "moved")
                replacement.rename(checkout)
            return super().resolve(strict=strict)

    with pytest.raises(OperationalRuleError) as excinfo:
        load_workflows(ReplacingPath(checkout))

    assert "replaced after its identity was read" in str(excinfo.value), str(
        excinfo.value
    )


@pytest.mark.skipif(
    not hasattr(os, "geteuid") or os.geteuid() == 0,
    reason="root and non-POSIX hosts bypass directory permissions",
)
def test_a_search_only_intermediate_directory_is_still_walked(
    tmp_path: pathlib.Path,
) -> None:
    """`.github` needs search permission, not read permission, to be walked.

    Opening an intermediate component read-only would fail with `EACCES` on a
    directory that grants only search, and the audit would stop before reading
    the workflows it is allowed to read. Only the final `workflows` component
    needs a readable handle.
    """
    checkout = tmp_path / "repo"
    workflow_directory = checkout / ".github" / "workflows"
    workflow_directory.mkdir(parents=True)
    (workflow_directory / "ci.yml").write_text("on: push\n", encoding="utf-8")
    github_directory = checkout / ".github"
    original_mode = github_directory.stat().st_mode
    github_directory.chmod(0o111)
    try:
        facts = load_workflows(checkout)
    finally:
        github_directory.chmod(original_mode)
    assert [fact["path"] for fact in facts] == [".github/workflows/ci.yml"], facts


def test_an_entry_that_appears_after_the_open_failure_is_refused(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A path occupied after the directory open failed is not an absence.

    The open reports a missing directory, and the probe that decides whether
    that meant absence runs afterwards. A checkout another process can write
    to may occupy the path in between; reporting the repository as having no
    workflows would turn that race into a compliant verdict.
    """
    checkout = tmp_path / "repo"
    workflow_directory = checkout / ".github" / "workflows"
    workflow_directory.parent.mkdir(parents=True)
    descend = workflow_evidence._descend

    def descend_then_occupy(parent: int, part: str, *, is_target: bool) -> int:
        """Occupy the workflow path, then raise the error the open raced with."""
        if part != "workflows":
            return descend(parent, part, is_target=is_target)
        workflow_directory.write_text("occupied\n", encoding="utf-8")
        raise FileNotFoundError(2, "No such file or directory", str(workflow_directory))

    monkeypatch.setattr(workflow_evidence, "_descend", descend_then_occupy)
    with pytest.raises(OperationalRuleError):
        load_workflows(checkout)
