"""Content errors the Markdown envelope's workflow reader must carry.

One workflow that cannot be decoded must become that file's indeterminate
evidence. An error that escapes the reader aborts the whole audit, and a loader
shared between documents lets the first failure decide every later verdict.
These cases live apart from the rest of the envelope's tests because they each
exercise a distinct failure in the decoding path rather than the envelope's
applicability or containment decisions.
"""

from __future__ import annotations

import typing as typ

from concordat.rules import markdown_envelope
from concordat.rules.markdown_envelope import (
    WorkflowFile,
    build_markdown_envelope,
    load_workflows,
    resolved_root,
)

if typ.TYPE_CHECKING:
    import pathlib

    import pytest


def _workflows(envelope: dict[str, object]) -> list[WorkflowFile]:
    """Return the envelope's workflow facts."""
    return typ.cast("list[WorkflowFile]", envelope["workflows"])


def test_a_document_json_cannot_carry_is_a_content_error(
    tmp_path: pathlib.Path,
) -> None:
    """A key JSON cannot represent is this file's error, not the audit's.

    A YAML date key decodes to a `datetime.date`, which `json.dumps` refuses
    with `TypeError`. The shared workflow reader records that as the file's
    indeterminate evidence, so this reader must too rather than letting one
    workflow abort the whole envelope.
    """
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "ci.yml").write_text("on: pull_request\n2026-01-01: value\n")
    fact = _workflows(typ.cast("dict[str, object]", build_markdown_envelope(tmp_path)))[
        0
    ]
    assert fact["parsed"] is None, fact
    assert "not JSON-safe" in str(fact["error"]), fact


def test_a_document_with_an_invalid_timestamp_is_a_content_error(
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
    facts = load_workflows(tmp_path, resolved_root(tmp_path))
    assert len(facts) == 1, facts
    assert facts[0]["parsed"] is None, facts
    assert "invalid YAML" in (facts[0]["error"] or ""), facts


def test_each_document_is_decoded_by_its_own_loader(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed parse must not decide every later document's verdict.

    ruamel does not guarantee that a `YAML` instance which raised can handle
    further input, so the envelope builds a loader per document rather than
    sharing module state across an audit.
    """
    directory = tmp_path / ".github" / "workflows"
    directory.mkdir(parents=True)
    (directory / "ci.yml").write_text("on: push\n", encoding="utf-8")
    (directory / "release.yml").write_text("on: push\n", encoding="utf-8")
    loaders: list[object] = []
    yaml_type = markdown_envelope.YAML

    def recording_yaml(*, typ: str | None = None) -> object:
        """Record the loader the envelope builds, then delegate to ruamel."""
        loader = yaml_type(typ=typ)
        loaders.append(loader)
        return loader

    monkeypatch.setattr(markdown_envelope, "YAML", recording_yaml)
    facts = load_workflows(tmp_path, resolved_root(tmp_path))
    assert len(facts) == 2, facts
    assert len(loaders) == 2, loaders
    assert loaders[0] is not loaders[1], loaders
