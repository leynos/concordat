"""Unit tests for the shared GitHub Actions workflow collector.

The collector is imported by more than one canon rule package, so the way it
reports a document it cannot decode is part of the contract those packages
build on: a bad file becomes that file's indeterminate evidence, never an
abort of the whole audit.
"""

from __future__ import annotations

import os
import typing as typ

import pytest

from concordat.errors import OperationalRuleError
from concordat.rules import workflow_evidence
from concordat.rules.workflow_evidence import load_workflows, read_text

if typ.TYPE_CHECKING:
    import pathlib


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


def test_an_out_of_range_timestamp_is_a_content_error(
    tmp_path: pathlib.Path,
) -> None:
    """A timestamp the loader cannot build is reported, not raised.

    `2026-02-30` fails inside the safe loader's timestamp constructor with a
    `ValueError` rather than a `YAMLError`. An uncaught one would abort the
    whole audit instead of carrying this file as indeterminate.
    """
    directory = tmp_path / ".github" / "workflows"
    directory.mkdir(parents=True)
    (directory / "a.yml").write_text(
        "on: push\nenv:\n  BUILD_DATE: 2026-02-30\n", encoding="utf-8"
    )
    facts = load_workflows(tmp_path)
    assert len(facts) == 1, facts
    assert facts[0]["parsed"] is None, facts
    assert "invalid YAML" in (facts[0]["error"] or ""), facts
