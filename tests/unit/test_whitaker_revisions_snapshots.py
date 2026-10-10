"""Hold the revision-list commands' output to its snapshots."""

from __future__ import annotations

import pathlib
import re
import typing as typ

import pytest

from tests.unit.whitaker_revisions_support import (
    History,
    run_main,
)

pytest_plugins = ("tests.unit.whitaker_revisions_fixtures",)


if typ.TYPE_CHECKING:
    import collections.abc as cabc


SNAPSHOTS: typ.Final = pathlib.Path(__file__).parent / "snapshots"


def _redacted(text: str, *, root: str, later: str, manifest: pathlib.Path) -> str:
    """Replace the run-specific ids, paths and counts with stable placeholders."""
    redacted = (
        text
        .replace(root, "<root>")
        .replace(later, "<later>")
        .replace(str(manifest), "<manifest>")
    )
    return re.sub(r": \d+ revisions", ": <count> revisions", redacted)


@pytest.fixture
def run_redacted(
    rule_package: tuple[pathlib.Path, str, str],
    history: History,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> cabc.Callable[[str], str]:
    """Return a runner that executes a command and yields its redacted output."""
    package, root, later = rule_package
    clone = pathlib.Path(history.repository.workdir)

    def run(command: str) -> str:
        run_main(monkeypatch, clone, command)
        return _redacted(
            capsys.readouterr().out,
            root=root,
            later=later,
            manifest=package / "rule.yaml",
        )

    return run


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(("list", "whitaker_revisions_list.txt"), id="list"),
        pytest.param(("check", "whitaker_revisions_check_drift.txt"), id="check-drift"),
        pytest.param(("sync", "whitaker_revisions_sync.txt"), id="sync"),
    ],
)
def test_the_command_output_matches_its_snapshot(
    run_redacted: cabc.Callable[[str], str],
    case: tuple[str, str],
) -> None:
    """The text a maintainer reads from each command keeps a stable shape.

    The snapshot holds placeholders for the run-specific commit ids and the
    temporary manifest path; semantic assertions for membership and status sit
    in the tests above, and the live derived list is not snapshotted.
    """
    command, snapshot = case

    assert run_redacted(command) == (SNAPSHOTS / snapshot).read_text(encoding="utf-8")
