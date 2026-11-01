"""Regression tests for bounded subprocess lifetime and output capture."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
import typing as typ

import pytest

from concordat import bounded_subprocess

if typ.TYPE_CHECKING:
    import pathlib


def _process_is_running(pid: int) -> bool:
    """Report whether a POSIX process is alive rather than a reaped zombie."""
    ps_path = shutil.which("ps")
    if ps_path is None:
        pytest.skip("ps is required to verify POSIX process-group cleanup")
    result = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - fixed argv, no shell
        [ps_path, "-o", "stat=", "-p", str(pid)],
        capture_output=True,
        check=False,
        shell=False,
        text=True,
    )
    state = result.stdout.strip()
    return bool(state) and not state.startswith("Z")


@pytest.mark.parametrize("limit", [1, 2, 3, 8])
def test_short_stderr_limits_keep_a_recognizable_truncation_marker(limit: int) -> None:
    """Even a one-byte cap must say that stderr was truncated."""
    result = bounded_subprocess._stderr_bytes(b"x" * limit, limit, overflowed=True)

    assert len(result) <= limit
    assert result.endswith(b"!")


@pytest.mark.skipif(os.name == "nt", reason="requires POSIX process groups")
def test_timeout_kills_a_descendant_that_keeps_the_pipes_open(
    tmp_path: pathlib.Path,
) -> None:
    """A timed-out child cannot leave a process alive through inherited pipes."""
    descendant_pid_path = tmp_path / "descendant.pid"
    descendant_code = "import time; time.sleep(10)"
    code = (
        "import pathlib, subprocess, sys, time; "
        "descendant = subprocess.Popen([sys.executable, '-c', "
        f"{descendant_code!r}]); "
        f"pathlib.Path({str(descendant_pid_path)!r}).write_text(str(descendant.pid)); "
        "time.sleep(10)"
    )
    started = time.monotonic()

    with pytest.raises(subprocess.TimeoutExpired):
        bounded_subprocess.run_bounded(
            [sys.executable, "-c", code],
            timeout=1.0,
        )

    assert time.monotonic() - started < 2.5
    descendant_pid = int(descendant_pid_path.read_text())
    deadline = time.monotonic() + 0.5
    while time.monotonic() < deadline:
        if not _process_is_running(descendant_pid):
            break
        time.sleep(0.01)

    assert not _process_is_running(descendant_pid)


@pytest.mark.skipif(os.name == "nt", reason="requires POSIX process groups")
def test_timeout_kills_a_descendant_after_its_parent_exits(
    tmp_path: pathlib.Path,
) -> None:
    """A dead process-group leader does not stop cleanup of its descendants."""
    descendant_pid_path = tmp_path / "orphaned-descendant.pid"
    descendant_code = "import time; time.sleep(10)"
    code = (
        "import pathlib, subprocess, sys; "
        "descendant = subprocess.Popen([sys.executable, '-c', "
        f"{descendant_code!r}]); "
        f"pathlib.Path({str(descendant_pid_path)!r}).write_text(str(descendant.pid))"
    )

    with pytest.raises(subprocess.TimeoutExpired):
        bounded_subprocess.run_bounded(
            [sys.executable, "-c", code],
            timeout=0.5,
        )

    descendant_pid = int(descendant_pid_path.read_text())
    deadline = time.monotonic() + 0.5
    while time.monotonic() < deadline and _process_is_running(descendant_pid):
        time.sleep(0.01)

    assert not _process_is_running(descendant_pid)


@pytest.mark.skipif(os.name == "nt", reason="requires POSIX process groups")
def test_keyboard_interrupt_kills_a_descendant(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """A caller interrupt does not leave the child process tree running."""
    descendant_pid_path = tmp_path / "interrupt-descendant.pid"
    descendant_code = "import time; time.sleep(2)"
    code = (
        "import pathlib, subprocess, sys, time; "
        "descendant = subprocess.Popen([sys.executable, '-c', "
        f"{descendant_code!r}]); "
        f"pathlib.Path({str(descendant_pid_path)!r}).write_text(str(descendant.pid)); "
        "time.sleep(2)"
    )

    def interrupt(_state: object) -> None:
        """Interrupt after the child has started and written its descendant PID."""
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline and not descendant_pid_path.exists():
            time.sleep(0.01)
        raise KeyboardInterrupt

    monkeypatch.setattr(bounded_subprocess, "_finish_process", interrupt)
    started = time.monotonic()
    with pytest.raises(KeyboardInterrupt):
        bounded_subprocess.run_bounded(
            [sys.executable, "-c", code],
            timeout=5,
        )

    assert time.monotonic() - started < 1
    descendant_pid = int(descendant_pid_path.read_text())
    assert not _process_is_running(descendant_pid)


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
