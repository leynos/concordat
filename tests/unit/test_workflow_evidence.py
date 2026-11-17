"""Unit tests for the shared GitHub Actions workflow collector.

The collector is imported by more than one canon rule package, so the way it
reports a document it cannot decode is part of the contract those packages
build on: a bad file becomes that file's indeterminate evidence, never an
abort of the whole audit.
"""

from __future__ import annotations

import typing as typ

from concordat.rules.workflow_evidence import load_workflows

if typ.TYPE_CHECKING:
    import pathlib


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
