"""Regression tests for bounded subprocess lifetime and output capture."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time

import pytest

from concordat import bounded_subprocess


@pytest.mark.parametrize("limit", [1, 2, 3, 8])
def test_short_stderr_limits_keep_a_recognizable_truncation_marker(limit: int) -> None:
    """Even a one-byte cap must say that stderr was truncated."""
    result = bounded_subprocess._stderr_bytes(b"x" * limit, limit, overflowed=True)

    assert len(result) <= limit
    assert result.endswith(b"!")


@pytest.mark.skipif(os.name == "nt", reason="requires POSIX process groups")
def test_timeout_kills_a_descendant_that_keeps_the_pipes_open() -> None:
    """A child cannot extend the parent's timeout by inheriting its pipes."""
    code = (
        "import subprocess, sys, time; "
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(2)']); "
        "time.sleep(2)"
    )
    started = time.monotonic()

    with pytest.raises(subprocess.TimeoutExpired):
        bounded_subprocess.run_bounded(
            [sys.executable, "-c", code],
            timeout=0.05,
        )

    assert time.monotonic() - started < 0.5


def test_reader_start_failure_terminates_the_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pipe-reader startup error must not wait for the child naturally."""
    original_start = threading.Thread.start
    started_threads = 0

    def fail_second_start(thread: threading.Thread) -> None:
        nonlocal started_threads
        started_threads += 1
        if started_threads == 2:
            raise RuntimeError
        original_start(thread)

    monkeypatch.setattr(threading.Thread, "start", fail_second_start)
    started = time.monotonic()

    with pytest.raises(RuntimeError):
        bounded_subprocess.run_bounded(
            [sys.executable, "-c", "import time; time.sleep(0.3)"],
            timeout=0.02,
        )

    assert time.monotonic() - started < 0.2
