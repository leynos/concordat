"""Unit tests for the `makeutil` pin hint attached to fixture drift failures."""

from __future__ import annotations

import typing as typ

import pytest

from tests.unit.makeutil_pin import drift_hint, pinned_release

if typ.TYPE_CHECKING:
    import pathlib

_DIGEST: typ.Final = "99dd28a138dbe07e88e4dc5dd3954e6b29b46cc959635311d326cb537253115d"
_WORKFLOW: typ.Final = (
    "env:\n"
    '  MAKEUTIL_VERSION: "0.1.0"\n'
    '  MAKEUTIL_ASSET: "makeutil-x86_64-unknown-linux-musl"\n'
    f'  MAKEUTIL_SHA256: "{_DIGEST}"\n'
)


def _workflow(tmp_path: pathlib.Path, body: str) -> pathlib.Path:
    """Write a workflow file with *body* and return its path."""
    path = tmp_path / "ci.yml"
    path.write_text(body, encoding="utf-8")
    return path


def test_the_pin_is_read_from_the_workflow(tmp_path: pathlib.Path) -> None:
    """The hint follows the workflow, so a repin never leaves it stale."""
    pin = pinned_release(_workflow(tmp_path, _WORKFLOW))
    assert (pin.version, pin.asset, pin.sha256) == (
        "0.1.0",
        "makeutil-x86_64-unknown-linux-musl",
        _DIGEST,
    ), pin


def test_the_hint_names_the_release_and_its_digest(tmp_path: pathlib.Path) -> None:
    """A failure that cannot name the pin sends the reader to compare blind."""
    hint = drift_hint(_workflow(tmp_path, _WORKFLOW))
    assert "v0.1.0" in hint, hint
    assert _DIGEST in hint, hint


@pytest.mark.parametrize(
    "missing", ["MAKEUTIL_VERSION", "MAKEUTIL_ASSET", "MAKEUTIL_SHA256"]
)
def test_a_workflow_missing_any_pin_variable_is_an_error(
    tmp_path: pathlib.Path, missing: str
) -> None:
    """A silently partial hint would be worse than none."""
    body = "".join(
        line + "\n" for line in _WORKFLOW.splitlines() if missing not in line
    )
    with pytest.raises(LookupError, match=missing):
        pinned_release(_workflow(tmp_path, body))


def test_the_repository_workflow_carries_a_pin() -> None:
    """The real workflow is the hint's source, so it must stay readable."""
    assert len(pinned_release().sha256) == 64
