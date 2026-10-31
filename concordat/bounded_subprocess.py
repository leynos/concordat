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


@dataclasses.dataclass(slots=True)
class _ProcessState:
    """Operation data shared by the bounded-process completion helpers."""

    process: subprocess.Popen[bytes]
    args: typ.Sequence[str | pathlib.Path]
    timeout: float
    max_bytes_per_stream: int
    stdout_capture: _PipeCapture
    stderr_capture: _PipeCapture
    readers: tuple[threading.Thread, ...]
    deadline: float


@dataclasses.dataclass(frozen=True, slots=True)
class _ProcessOutput:
    """Bounded output and reader state captured from both child pipes."""

    stdout: bytes
    stdout_overflowed: bool
    stdout_errors: tuple[OSError, ...]
    stderr: bytes
    stderr_overflowed: bool
    stderr_errors: tuple[OSError, ...]


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


def _kill_windows_process_tree(process: subprocess.Popen[bytes]) -> bool:
    """Stop a Windows child tree and report whether `taskkill` succeeded."""
    try:
        completed = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - fixed argv, no shell
            ["taskkill", "/F", "/T", "/PID", str(process.pid)],  # ruff: ignore[start-process-with-partial-path] - Windows inbox utility
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=_TREE_KILL_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def _kill_posix_process_group(process: subprocess.Popen[bytes]) -> bool:
    """Stop the dedicated POSIX process group and report whether it existed."""
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except OSError:
        return False
    return True


def _kill_process_tree(process: subprocess.Popen[bytes]) -> None:
    """Stop the child and descendants that may still own its output pipes."""
    tree_stopped = (
        _kill_windows_process_tree(process)
        if os.name == "nt"
        else _kill_posix_process_group(process)
    )
    if not tree_stopped and process.poll() is None:
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


def _capture_output(state: _ProcessState) -> _ProcessOutput:
    """Copy bounded output state and format the stderr truncation marker."""
    stdout, stdout_overflowed, stdout_errors = _capture_snapshot(state.stdout_capture)
    stderr_data, stderr_overflowed, stderr_errors = _capture_snapshot(
        state.stderr_capture
    )
    stderr = _stderr_bytes(
        stderr_data,
        state.max_bytes_per_stream,
        overflowed=stderr_overflowed,
    )
    return _ProcessOutput(
        stdout=stdout,
        stdout_overflowed=stdout_overflowed,
        stdout_errors=stdout_errors,
        stderr=stderr,
        stderr_overflowed=stderr_overflowed,
        stderr_errors=stderr_errors,
    )


def _raise_pipe_error(output: _ProcessOutput) -> None:
    """Propagate the first error raised while draining a child pipe."""
    errors = output.stdout_errors or output.stderr_errors
    if errors:
        raise errors[0]


def _raise_process_timeout(
    state: _ProcessState,
    output: _ProcessOutput,
    *,
    timed_out: bool,
) -> None:
    """Raise the timeout error with bounded child output when needed."""
    if timed_out:
        raise subprocess.TimeoutExpired(
            state.args,
            state.timeout,
            output=output.stdout,
            stderr=output.stderr,
        )


def _raise_stdout_overflow(state: _ProcessState, output: _ProcessOutput) -> None:
    """Reject machine-readable stdout that exceeded its capture limit."""
    if output.stdout_overflowed:
        raise OutputLimitExceededError("stdout", state.max_bytes_per_stream)


def _process_returncode(
    state: _ProcessState,
    output: _ProcessOutput,
    *,
    timed_out: bool,
) -> int:
    """Raise capture/timeout errors, or return the child exit code."""
    _raise_pipe_error(output)
    _raise_process_timeout(state, output, timed_out=timed_out)
    _raise_stdout_overflow(state, output)
    returncode = state.process.returncode
    if returncode is None:
        raise RuntimeError
    return returncode


def _complete_process(
    state: _ProcessState,
    *,
    timed_out: bool,
) -> subprocess.CompletedProcess[str]:
    """Build the result or raise the bounded process error."""
    output = _capture_output(state)
    returncode = _process_returncode(state, output, timed_out=timed_out)

    return subprocess.CompletedProcess(
        state.args,
        returncode,
        stdout=_decode(output.stdout),
        stderr=_decode(
            output.stderr,
            errors="replace" if output.stderr_overflowed else "strict",
        ),
    )


def _finish_process(state: _ProcessState) -> subprocess.CompletedProcess[str]:
    """Wait within the deadline, stop held-open pipes, and collect output."""
    timed_out = _wait_for_process(state.process, state.deadline)
    if timed_out or not _join_readers(state.readers, state.deadline):
        timed_out = True
        _stop_readers_after_timeout(state.process, state.readers)
    return _complete_process(state, timed_out=timed_out)


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
        If the per-stream byte limit is not positive. The adapter also raises
        ``RuntimeError`` when the pipes cannot be created,
        :class:`OutputLimitExceededError` on stdout overflow, and
        ``TimeoutExpired`` when the child exceeds *timeout*.
    """
    if max_bytes_per_stream <= 0:
        raise ValueError

    deadline = time.monotonic() + timeout
    with _start_process(args, cwd) as process:
        stdout_capture, stderr_capture, readers = _start_readers(
            process, max_bytes_per_stream
        )
        state = _ProcessState(
            process,
            args,
            timeout,
            max_bytes_per_stream,
            stdout_capture,
            stderr_capture,
            readers,
            deadline,
        )
        return _finish_process(state)
