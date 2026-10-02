"""Unit tests for the `makeutil` pin hint attached to fixture drift failures."""

from __future__ import annotations

import typing as typ

import pytest

from tests.unit.makeutil_pin import (
    assert_matches_recorded,
    drift_hint,
    pinned_release,
)

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


def test_the_hint_is_the_whole_message_for_a_fixed_pin(tmp_path: pathlib.Path) -> None:
    """The full text is pinned, so losing the asset or the digest is caught."""
    assert drift_hint(_workflow(tmp_path, _WORKFLOW)) == (
        "before blaming the fixtures, check the local makeutil against the "
        "pinned release v0.1.0: `makeutil --version` does not distinguish "
        "revisions, so compare the SHA-256 of the "
        "makeutil-x86_64-unknown-linux-musl asset from the leynos/makeutil "
        f"release with {_DIGEST}"
    )


def test_the_hint_follows_a_pin_that_is_not_0_1_0(tmp_path: pathlib.Path) -> None:
    """A repin must not leave the old version in the message."""
    body = _WORKFLOW.replace('"0.1.0"', '"0.2.0"')
    hint = drift_hint(_workflow(tmp_path, body))
    assert "v0.2.0" in hint, hint
    assert "0.1.0" not in hint, hint


@pytest.mark.parametrize(
    "body",
    [
        pytest.param("env: {}\n", id="no-pin"),
        pytest.param("\udcff", id="not-decodable"),
    ],
)
def test_an_unreadable_pin_degrades_to_a_sentence(
    tmp_path: pathlib.Path, body: str
) -> None:
    """The hint is built inside a failing assertion, so it must not raise."""
    path = tmp_path / "ci.yml"
    path.write_bytes(body.encode("utf-8", "surrogateescape"))
    assert "could not be read" in drift_hint(path)
    assert "could not be read" in drift_hint(tmp_path / "absent.yml")


def test_a_matching_comparison_passes_silently(tmp_path: pathlib.Path) -> None:
    """The check adds nothing when the checked-in value is current."""
    assert_matches_recorded(
        {"a": 1}, {"a": 1}, "envelope x", _workflow(tmp_path, _WORKFLOW)
    )


def test_a_failing_comparison_reports_the_pin(tmp_path: pathlib.Path) -> None:
    """The fixture-comparison failure itself carries version, asset and digest."""
    workflow = _workflow(tmp_path, _WORKFLOW)
    with pytest.raises(AssertionError) as raised:
        assert_matches_recorded({"a": 1}, {"a": 2}, "envelope x", workflow)
    message = str(raised.value)
    for expected in (
        "envelope x",
        "v0.1.0",
        "makeutil-x86_64-unknown-linux-musl",
        _DIGEST,
    ):
        assert expected in message, message


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
