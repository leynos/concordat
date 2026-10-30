"""Run a subprocess while bounding the output retained from each stream.

Both pipes are drained concurrently so a child cannot deadlock by filling the
pipe that the parent is not currently reading. Retained data is capped per
stream; excess bytes are discarded while the child runs.
"""

from __future__ import annotations

import dataclasses
import locale
import subprocess
import threading
import typing as typ

if typ.TYPE_CHECKING:
    import pathlib

MAX_CAPTURE_BYTES_PER_STREAM: typ.Final = 1_048_576
"""Maximum retained bytes from either child stream (1 MiB)."""

_CHUNK_SIZE: typ.Final = 64 * 1024
_STDERR_TRUNCATION_MARKER: typ.Final = b"\n...[stderr truncated]"


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


def _drain_pipe(
    pipe: typ.BinaryIO,
    capture: _PipeCapture,
    limit_bytes: int,
) -> None:
    """Drain *pipe*, retaining no more than *limit_bytes*."""
    try:
        while chunk := pipe.read(_CHUNK_SIZE):
            remaining = limit_bytes - len(capture.data)
            if remaining > 0:
                capture.data.extend(chunk[:remaining])
            if len(chunk) > max(remaining, 0):
                capture.overflowed = True
    except OSError as error:
        capture.errors.append(error)
    finally:
        pipe.close()


def _decode(data: bytes, *, errors: str = "strict") -> str:
    """Decode text like ``subprocess.run(text=True)`` on this platform."""
    encoding = locale.getpreferredencoding(do_setlocale=False)
    return (
        data.decode(encoding, errors=errors).replace("\r\n", "\n").replace("\r", "\n")
    )


def _stderr_bytes(capture: _PipeCapture, limit_bytes: int) -> bytes:
    """Return captured stderr with a truncation marker inside the byte cap."""
    if not capture.overflowed:
        return bytes(capture.data)
    marker = _STDERR_TRUNCATION_MARKER[:limit_bytes]
    prefix_bytes = max(0, limit_bytes - len(marker))
    return bytes(capture.data[:prefix_bytes]) + marker


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

    with subprocess.Popen(  # ruff: ignore[subprocess-without-shell-equals-true] - fixed argv, no shell
        args,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
    ) as process:
        if process.stdout is None or process.stderr is None:
            raise RuntimeError

        stdout_capture = _PipeCapture()
        stderr_capture = _PipeCapture()
        stdout_reader = threading.Thread(
            target=_drain_pipe,
            args=(process.stdout, stdout_capture, max_bytes_per_stream),
            daemon=True,
        )
        stderr_reader = threading.Thread(
            target=_drain_pipe,
            args=(process.stderr, stderr_capture, max_bytes_per_stream),
            daemon=True,
        )
        stdout_reader.start()
        stderr_reader.start()

        timed_out = False
        try:
            returncode = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            process.kill()
            returncode = process.wait()
        stdout_reader.join()
        stderr_reader.join()

        for capture in (stdout_capture, stderr_capture):
            if capture.errors:
                raise capture.errors[0]

        stdout = bytes(stdout_capture.data)
        stderr = _stderr_bytes(stderr_capture, max_bytes_per_stream)
        if timed_out:
            raise subprocess.TimeoutExpired(
                args,
                timeout,
                output=stdout,
                stderr=stderr,
            )
        if stdout_capture.overflowed:
            raise OutputLimitExceededError("stdout", max_bytes_per_stream)

        return subprocess.CompletedProcess(
            args,
            returncode,
            stdout=_decode(stdout),
            stderr=_decode(
                stderr,
                errors="replace" if stderr_capture.overflowed else "strict",
            ),
        )
