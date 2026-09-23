"""Tests for the makeutil pin reachability check.

Each test builds a real Git repository standing in for makeutil, so the check
runs its own fetch and ancestry query rather than a mocked one. The orphan
case reproduces how concordat's original pin was lost: a commit on a
pull-request branch that main never contains.
"""

from __future__ import annotations

import dataclasses
import os
import subprocess
import sys
import typing as typ
from pathlib import Path

import pytest

from scripts import check_makeutil_pin as pin_check

_SCRIPT: typ.Final = Path(pin_check.__file__)
_IDENTITY: typ.Final = ("-c", "user.name=Test", "-c", "user.email=test@example.com")


def _git(repository: Path, *arguments: str) -> str:
    """Run git in *repository* and return its stripped standard output."""
    completed = subprocess.run(  # noqa: S603 - fixed git argv in a temp repo
        ["git", *_IDENTITY, *arguments],  # noqa: S607 - git from PATH
        cwd=repository,
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout.strip()


def _commit(repository: Path, message: str) -> str:
    """Record an empty commit and return its SHA."""
    _git(repository, "commit", "--quiet", "--allow-empty", "-m", message)
    return _git(repository, "rev-parse", "HEAD")


@dataclasses.dataclass(frozen=True, slots=True)
class Upstream:
    """A stand-in makeutil repository and the commits the tests pin."""

    url: str
    older_main: str
    main_head: str
    orphan: str


@pytest.fixture
def upstream(tmp_path: Path) -> Upstream:
    """Build a repository whose main has two commits and a side branch one."""
    repository = tmp_path / "makeutil"
    repository.mkdir()
    _git(repository, "init", "--quiet", "--initial-branch=main")
    older_main = _commit(repository, "first")
    _git(repository, "switch", "--quiet", "-c", "pr-branch")
    orphan = _commit(repository, "pull-request work main never received")
    _git(repository, "switch", "--quiet", "main")
    main_head = _commit(repository, "second")
    return Upstream(repository.as_uri(), older_main, main_head, orphan)


@pytest.mark.parametrize("pinned", ["main_head", "older_main"])
def test_pin_on_main_passes(upstream: Upstream, pinned: str) -> None:
    """The head of main and any earlier main commit are both accepted."""
    pin_check.check_pin(getattr(upstream, pinned), upstream.url, "main")


def test_pin_off_main_is_refused(upstream: Upstream) -> None:
    """A commit only a pull-request branch reaches is refused.

    The side branch still exists upstream, so the commit is fetchable; the
    check must still refuse it because main does not contain it.
    """
    with pytest.raises(pin_check.PinCheckError, match="not reachable from main"):
        pin_check.check_pin(upstream.orphan, upstream.url, "main")


def test_unknown_pin_is_refused(upstream: Upstream) -> None:
    """A SHA the repository has never held is refused, not waved through."""
    with pytest.raises(pin_check.PinCheckError, match="not reachable from main"):
        pin_check.check_pin("0" * 40, upstream.url, "main")


def test_unreachable_repository_is_refused(tmp_path: Path) -> None:
    """A failed fetch fails the check instead of skipping it."""
    missing = (tmp_path / "absent").as_uri()
    with pytest.raises(pin_check.PinCheckError, match="could not fetch main"):
        pin_check.check_pin("0" * 40, missing, "main")


@pytest.mark.parametrize(
    ("pinned", "expected_status"),
    [("main_head", 0), ("orphan", 1)],
)
def test_script_reads_the_pin_from_the_environment(
    upstream: Upstream,
    pinned: str,
    expected_status: int,
) -> None:
    """The script run as CI runs it takes its inputs from ``MAKEUTIL_*``.

    The environment is set on the child process only.
    """
    environment = {
        **os.environ,
        "MAKEUTIL_REVISION": getattr(upstream, pinned),
        "MAKEUTIL_REPOSITORY": upstream.url,
    }
    completed = subprocess.run(  # noqa: S603 - fixed interpreter and script
        [sys.executable, str(_SCRIPT)],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == expected_status, completed.stderr


@pytest.mark.parametrize(
    ("pinned", "expected_status", "expected_stream"),
    [("main_head", 0, "out"), ("orphan", 1, "err")],
)
def test_main_reports_the_outcome_as_an_exit_status(
    upstream: Upstream,
    capsys: pytest.CaptureFixture[str],
    pinned: str,
    expected_status: int,
    expected_stream: str,
) -> None:
    """``main`` returns 0 with a confirmation, or 1 with the reason on stderr."""
    revision = getattr(upstream, pinned)
    status = pin_check.main(revision=revision, repository=upstream.url)
    captured = capsys.readouterr()
    assert status == expected_status, captured
    assert revision in getattr(captured, expected_stream), captured


def test_scratch_repository_failure_is_refused(
    monkeypatch: pytest.MonkeyPatch,
    upstream: Upstream,
) -> None:
    """A scratch repository that cannot be created fails the check.

    Nothing has been fetched at that point, so passing would vouch for a pin
    the check never examined.
    """

    def failing_git(*arguments: str, cwd: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            ["git", *arguments], 128, stdout="", stderr=f"cannot init in {cwd}"
        )

    monkeypatch.setattr(pin_check, "_git", failing_git)
    with pytest.raises(pin_check.PinCheckError, match="scratch repository"):
        pin_check.check_pin(upstream.main_head, upstream.url, "main")
