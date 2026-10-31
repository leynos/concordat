"""Run a subprocess while bounding the output retained from each stream.

Both pipes are drained concurrently so a child cannot deadlock by filling the
pipe that the parent is not currently reading. Retained data is capped per
stream; excess bytes are discarded while the child runs.
"""

from __future__ import annotations

import contextlib
import dataclasses
import locale
import os
import signal
import subprocess
import threading
import time
import typing as typ

if typ.TYPE_CHECKING:
    import pathlib

MAX_CAPTURE_BYTES_PER_STREAM: typ.Final = 1_048_576
"""Maximum retained bytes from either child stream (1 MiB)."""

_CHUNK_SIZE: typ.Final = 64 * 1024
_STDERR_TRUNCATION_MARKER: typ.Final = b"\n...[stderr truncated]"
_SHORT_TRUNCATION_MARKER: typ.Final = b"!"
_READER_CLEANUP_SECONDS: typ.Final = 0.1
_TREE_KILL_TIMEOUT_SECONDS: typ.Final = 1.0


class OutputLimitExceededError(RuntimeError):
    """Raised after a child writes more than the stdout retention limit."""

    def __init__(self, stream: str, limit_bytes: int) -> None:
        super().__init__(f"subprocess {stream} exceeded {limit_bytes} byte limit")
        self.stream = stream
        self.limit_bytes = limit_bytes


@dataclasses.dataclass(slots=True)
class _PipeCapture:
    """Mutable capture state for one pipe reader."""

    data: bytearray = dataclasses.field(default_factory=bytearray)
    overflowed: bool = False
    errors: list[OSError] = dataclasses.field(default_factory=list)
    lock: threading.Lock = dataclasses.field(default_factory=threading.Lock, repr=False)


def _drain_pipe(
    pipe: typ.BinaryIO,
    capture: _PipeCapture,
    limit_bytes: int,
) -> None:
    """Drain *pipe*, retaining no more than *limit_bytes*."""
    try:
        while chunk := pipe.read(_CHUNK_SIZE):
            with capture.lock:
                remaining = limit_bytes - len(capture.data)
                if remaining > 0:
                    capture.data.extend(chunk[:remaining])
                if len(chunk) > max(remaining, 0):
                    capture.overflowed = True
    except OSError as error:
        with capture.lock:
            capture.errors.append(error)
    finally:
        pipe.close()


def _decode(data: bytes, *, errors: str = "strict") -> str:
    """Decode text like ``subprocess.run(text=True)`` on this platform."""
    encoding = locale.getpreferredencoding(do_setlocale=False)
    return (
        data.decode(encoding, errors=errors).replace("\r\n", "\n").replace("\r", "\n")
    )


def _stderr_bytes(data: bytes, limit_bytes: int, *, overflowed: bool) -> bytes:
    """Return captured stderr with a truncation marker inside the byte cap.

    Limits too small for the full marker use a single ``!`` byte so even the
    smallest supported capture remains recognisable as truncated.

    Returns
    -------
    bytes
        Captured stderr with a truncation marker inside the byte cap.
    """
    if not overflowed:
        return data
    marker = (
        _STDERR_TRUNCATION_MARKER
        if limit_bytes >= len(_STDERR_TRUNCATION_MARKER)
        else _SHORT_TRUNCATION_MARKER
    )
    prefix_bytes = max(0, limit_bytes - len(marker))
    return data[:prefix_bytes] + marker


def _start_process(
    args: typ.Sequence[str | pathlib.Path],
    cwd: str | pathlib.Path | None,
) -> subprocess.Popen[bytes]:
    """Start an argv-only child in a group that can be stopped on timeout."""
    if os.name == "nt":
        return subprocess.Popen(  # ruff: ignore[subprocess-without-shell-equals-true] - fixed argv, no shell
            args,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
    return subprocess.Popen(  # ruff: ignore[subprocess-without-shell-equals-true] - fixed argv, no shell
        args,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        start_new_session=True,
    )


def _kill_process_tree(process: subprocess.Popen[bytes]) -> None:
    """Stop the child and descendants that may still own its output pipes."""
    if os.name == "nt":
        try:
            completed = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - fixed argv, no shell
                ["taskkill", "/F", "/T", "/PID", str(process.pid)],  # ruff: ignore[start-process-with-partial-path] - Windows inbox utility
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=_TREE_KILL_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            completed = None
        if completed is not None and completed.returncode == 0:
            return
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except OSError:
            pass
        else:
            return
    if process.poll() is None:
        with contextlib.suppress(ProcessLookupError):
            process.kill()


def _join_readers(readers: tuple[threading.Thread, ...], deadline: float) -> bool:
    """Join readers only within the remaining operation or cleanup budget."""
    for reader in readers:
        reader.join(timeout=max(0.0, deadline - time.monotonic()))
    return all(not reader.is_alive() for reader in readers)


def _capture_snapshot(capture: _PipeCapture) -> tuple[bytes, bool, tuple[OSError, ...]]:
    """Copy a reader's bounded state without racing a final pipe read."""
    with capture.lock:
        return bytes(capture.data), capture.overflowed, tuple(capture.errors)


def _start_readers(
    process: subprocess.Popen[bytes], limit_bytes: int
) -> tuple[_PipeCapture, _PipeCapture, tuple[threading.Thread, ...]]:
    """Start one draining thread for each child stream."""
    if process.stdout is None or process.stderr is None:
        raise RuntimeError

    stdout_capture = _PipeCapture()
    stderr_capture = _PipeCapture()
    readers = (
        threading.Thread(
            target=_drain_pipe,
            args=(process.stdout, stdout_capture, limit_bytes),
            daemon=True,
        ),
        threading.Thread(
            target=_drain_pipe,
            args=(process.stderr, stderr_capture, limit_bytes),
            daemon=True,
        ),
    )
    started: list[threading.Thread] = []
    try:
        for reader in readers:
            reader.start()
            started.append(reader)
    except BaseException:
        _kill_process_tree(process)
        process.wait()
        _join_readers(tuple(started), time.monotonic() + _READER_CLEANUP_SECONDS)
        raise
    return stdout_capture, stderr_capture, readers


def _wait_for_process(process: subprocess.Popen[bytes], deadline: float) -> bool:
    """Wait only until *deadline*, killing the process group on expiry."""
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        _kill_process_tree(process)
        process.wait()
        return True
    try:
        process.wait(timeout=remaining)
    except subprocess.TimeoutExpired:
        _kill_process_tree(process)
        process.wait()
        return True
    return False


def _stop_readers_after_timeout(
    process: subprocess.Popen[bytes],
    readers: tuple[threading.Thread, ...],
) -> None:
    """Kill descendants and give pipe readers a short bounded cleanup window."""
    _kill_process_tree(process)
    process.wait()
    _join_readers(readers, time.monotonic() + _READER_CLEANUP_SECONDS)


def run_bounded(
    args: typ.Sequence[str | pathlib.Path],
    *,
    timeout: float,
    cwd: str | pathlib.Path | None = None,
    max_bytes_per_stream: int = MAX_CAPTURE_BYTES_PER_STREAM,
) -> subprocess.CompletedProcess[str]:
    """Run *args* and retain at most *max_bytes_per_stream* from each pipe.

    Stdout is treated as machine-readable data: if it exceeds the cap, the
    child is fully drained and waited for, then :class:`OutputLimitExceededError`
    is raised. Stderr is diagnostic text, so its prefix is retained with an
    explicit truncation marker. A timeout raises ``TimeoutExpired`` with only
    the bounded output captured before termination.

    Returns
    -------
    subprocess.CompletedProcess[str]
        The child status and bounded, decoded stdout and stderr.

    Raises
    ------
    ValueError
        If the per-stream byte limit is not positive.
    RuntimeError
        If the process pipes cannot be created.
    OutputLimitExceededError
        If stdout exceeds the retention limit.
    subprocess.TimeoutExpired
        If the process does not finish before *timeout*.
    """
    if max_bytes_per_stream <= 0:
        raise ValueError

    deadline = time.monotonic() + timeout
    with _start_process(args, cwd) as process:
        stdout_capture, stderr_capture, readers = _start_readers(
            process, max_bytes_per_stream
        )
        timed_out = _wait_for_process(process, deadline)
        if not _join_readers(readers, deadline):
            timed_out = True
            _stop_readers_after_timeout(process, readers)
        elif timed_out:
            _stop_readers_after_timeout(process, readers)

        stdout, stdout_overflowed, stdout_errors = _capture_snapshot(stdout_capture)
        stderr_data, stderr_overflowed, stderr_errors = _capture_snapshot(
            stderr_capture
        )
        stderr = _stderr_bytes(
            stderr_data,
            max_bytes_per_stream,
            overflowed=stderr_overflowed,
        )
        if stdout_errors or stderr_errors:
            raise (stdout_errors or stderr_errors)[0]
        if timed_out:
            raise subprocess.TimeoutExpired(args, timeout, output=stdout, stderr=stderr)
        if stdout_overflowed:
            raise OutputLimitExceededError("stdout", max_bytes_per_stream)
        returncode = process.returncode
        if returncode is None:
            raise RuntimeError

        return subprocess.CompletedProcess(
            args,
            returncode,
            stdout=_decode(stdout),
            stderr=_decode(
                stderr,
                errors="replace" if stderr_overflowed else "strict",
            ),
        )
