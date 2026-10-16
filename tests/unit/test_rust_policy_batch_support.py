"""Specify storage failures at the root Rust-policy batch boundary."""

from __future__ import annotations

import json
import subprocess
import typing as typ

import pytest

from concordat.errors import OperationalRuleError
from tests.unit import rust_policy_batch_support as batch_support

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    import pathlib
    import types

    from tests.unit.rust_policy_batch_support import (
        _TemporaryDirectoryResource as _BatchDirectoryResource,
    )

_ENVELOPE: dict[str, object] = {"cargo": {}, "clippy": {}}
_BATCH_DIRECTORY_PREFIX = "concordat-rust-policy-"
_ROOT_POLICY_NAMESPACE = "canon.rust.lints"
_SUCCESSFUL_ROOT_POLICY_STDOUT = json.dumps([
    {
        "filename": "envelope.json",
        "namespace": _ROOT_POLICY_NAMESPACE,
        "successes": 0,
    }
])


class _FakeTemporaryDirectoryResource:
    """Provide a directory name and record when its cleanup runs."""

    def __init__(self, name: str, events: list[str]) -> None:
        """Retain the scratch name and event log."""
        self.name = name
        self._events = events

    def cleanup(self) -> None:
        """Record one cleanup call."""
        self._events.append("cleanup")


class _CleanupFailingTemporaryDirectory:
    """Model both temporary-directory cleanup interfaces with one failure."""

    def __init__(self, name: pathlib.Path, failure: OSError, events: list[str]) -> None:
        """Retain the directory name, cleanup error, and call order."""
        self.name = str(name)
        self._failure = failure
        self._events = events

    def __enter__(self) -> str:
        """Return the directory name used by the original helper."""
        return self.name

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc_value: BaseException | None,
        _traceback: types.TracebackType | None,
    ) -> None:
        """Raise the configured error when a context manager cleans up."""
        self.cleanup()

    def cleanup(self) -> None:
        """Record cleanup and raise the configured error."""
        self._events.append("cleanup")
        raise self._failure


def _unexpected_write_text(
    _path: pathlib.Path,
    _contents: str,
    *,
    encoding: str = "utf-8",
) -> int:
    """Fail if a test reaches envelope writing unexpectedly."""
    pytest.fail(f"unexpected write_text call with encoding {encoding!r}")


def _unexpected_run_process(_argv: cabc.Sequence[str], **options: object) -> typ.Never:
    """Fail for every process call, retaining its arguments for diagnosis."""
    pytest.fail(f"unexpected process call with argv={_argv!r}, options={options!r}")


def _expected_conftest_argv(scratch: pathlib.Path) -> list[str]:
    """Return the root-policy command for a single written envelope."""
    return [
        "conftest",
        "test",
        "--policy",
        str(batch_support._ROOT_POLICY_DIR),
        "--namespace",
        _ROOT_POLICY_NAMESPACE,
        "--output",
        "json",
        str(scratch / "envelope.json"),
    ]


def test_root_policy_batch_reports_directory_creation_failure() -> None:
    """Report directory-creation failure before invoking Conftest."""
    failure = OSError("temporary storage is unavailable")
    create_calls: list[str] = []

    def create_directory(*, prefix: str) -> _BatchDirectoryResource:
        """Raise the configured failure before creating a directory."""
        create_calls.append(prefix)
        raise failure

    operations = batch_support._RootRustPolicyBatchOperations(
        create_directory=create_directory,
        write_text=_unexpected_write_text,
        run_process=_unexpected_run_process,
    )

    with pytest.raises(
        OperationalRuleError,
        match="could not create temporary directory",
    ) as captured:
        batch_support.invoke_root_rust_policy_batch([_ENVELOPE], operations=operations)

    error = captured.value
    assert error.__cause__ is failure
    assert error.operation == "create-batch-directory"
    assert error.tool == "conftest"
    assert error.resource == _BATCH_DIRECTORY_PREFIX
    assert create_calls == [_BATCH_DIRECTORY_PREFIX]


def test_root_policy_batch_reports_envelope_write_failure_before_conftest(
    tmp_path: pathlib.Path,
) -> None:
    """Report envelope-write failure before invoking Conftest."""
    failure = OSError("input storage is read-only")
    scratch = tmp_path / "rust-policy-batch"
    scratch.mkdir()
    events: list[str] = []
    resource = _FakeTemporaryDirectoryResource(str(scratch), events)
    create_calls: list[str] = []
    write_calls: list[tuple[pathlib.Path, str, str]] = []

    def create_directory(*, prefix: str) -> _BatchDirectoryResource:
        """Return the scratch resource after recording its prefix."""
        create_calls.append(prefix)
        events.append("create")
        return resource

    def write_text(
        path: pathlib.Path,
        contents: str,
        *,
        encoding: str = "utf-8",
    ) -> int:
        """Raise the configured failure after recording the write request."""
        write_calls.append((path, contents, encoding))
        events.append("write")
        raise failure

    operations = batch_support._RootRustPolicyBatchOperations(
        create_directory=create_directory,
        write_text=write_text,
        run_process=_unexpected_run_process,
    )

    with pytest.raises(
        OperationalRuleError,
        match="could not write root Rust-policy envelope",
    ) as captured:
        batch_support.invoke_root_rust_policy_batch([_ENVELOPE], operations=operations)

    error = captured.value
    assert len(write_calls) == 1
    write_path, contents, encoding = write_calls[0]
    assert error.__cause__ is failure
    assert error.operation == "write-batch-envelope"
    assert error.tool == "conftest"
    assert error.resource == write_path
    assert write_path.name == "envelope.json"
    assert contents == json.dumps(_ENVELOPE)
    assert encoding == "utf-8"
    assert create_calls == [_BATCH_DIRECTORY_PREFIX]
    assert events == ["create", "write", "cleanup"]


def test_root_policy_batch_reports_cleanup_failure_after_evaluation(
    tmp_path: pathlib.Path,
) -> None:
    """Report cleanup failure after successful batch evaluation."""
    failure = OSError("temporary storage cleanup failed")
    scratch = tmp_path / "rust-policy-batch"
    scratch.mkdir()
    events: list[str] = []
    temporary_directory = _CleanupFailingTemporaryDirectory(scratch, failure, events)
    create_calls: list[str] = []
    process_calls: list[tuple[list[str], bool, bool, float, bool]] = []

    def create_directory(*, prefix: str) -> _BatchDirectoryResource:
        """Return the scratch resource after recording its prefix."""
        create_calls.append(prefix)
        events.append("create")
        return temporary_directory

    def write_text(
        path: pathlib.Path,
        contents: str,
        *,
        encoding: str = "utf-8",
    ) -> int:
        """Write one envelope through the injected storage operation."""
        events.append("write")
        return path.write_text(contents, encoding=encoding)

    def run_process(
        argv: cabc.Sequence[str],
        *,
        capture_output: bool = True,
        text: bool = True,
        timeout: float = 120.0,
        check: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        """Return valid policy output through the injected process operation."""
        events.append("process")
        process_calls.append((list(argv), capture_output, text, timeout, check))
        return subprocess.CompletedProcess(
            args=list(argv),
            returncode=0,
            stdout=_SUCCESSFUL_ROOT_POLICY_STDOUT,
            stderr="",
        )

    operations = batch_support._RootRustPolicyBatchOperations(
        create_directory=create_directory,
        write_text=write_text,
        run_process=run_process,
    )

    with pytest.raises(
        OperationalRuleError,
        match="could not clean up root Rust-policy batch directory",
    ) as captured:
        batch_support.invoke_root_rust_policy_batch([_ENVELOPE], operations=operations)

    error = captured.value
    written_envelope = scratch / "envelope.json"
    assert error.__cause__ is failure
    assert error.operation == "cleanup-batch-directory"
    assert error.tool == "conftest"
    assert error.resource == str(scratch)
    assert create_calls == [_BATCH_DIRECTORY_PREFIX]
    assert process_calls == [
        (_expected_conftest_argv(scratch), True, True, 120.0, False)
    ]
    assert events == ["create", "write", "process", "cleanup"]
    assert written_envelope.read_text(encoding="utf-8") == json.dumps(_ENVELOPE)


@pytest.mark.parametrize(
    ("failure", "expected_detail"),
    [
        pytest.param(
            FileNotFoundError("conftest"),
            "conftest was not found on PATH",
            id="missing-executable",
        ),
        pytest.param(
            subprocess.TimeoutExpired(cmd="conftest", timeout=120.0),
            "conftest timed out after 120s",
            id="timeout",
        ),
    ],
)
def test_root_policy_batch_reports_process_failures_through_operations(
    tmp_path: pathlib.Path,
    failure: FileNotFoundError | subprocess.TimeoutExpired,
    expected_detail: str,
) -> None:
    """Translate missing Conftest and timeout failures from the process seam."""
    scratch = tmp_path / "rust-policy-batch"
    scratch.mkdir()
    events: list[str] = []
    resource = _FakeTemporaryDirectoryResource(str(scratch), events)
    process_calls: list[tuple[list[str], bool, bool, float, bool]] = []

    def create_directory(*, prefix: str) -> _BatchDirectoryResource:
        """Return the scratch resource after checking the requested prefix."""
        assert prefix == _BATCH_DIRECTORY_PREFIX
        events.append("create")
        return resource

    def write_text(
        path: pathlib.Path,
        contents: str,
        *,
        encoding: str = "utf-8",
    ) -> int:
        """Write one envelope through the injected storage operation."""
        events.append("write")
        return path.write_text(contents, encoding=encoding)

    def run_process(
        argv: cabc.Sequence[str],
        *,
        capture_output: bool = True,
        text: bool = True,
        timeout: float = 120.0,
        check: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        """Raise the supplied process failure after recording the call."""
        events.append("process")
        process_calls.append((list(argv), capture_output, text, timeout, check))
        raise failure

    operations = batch_support._RootRustPolicyBatchOperations(
        create_directory=create_directory,
        write_text=write_text,
        run_process=run_process,
    )

    with pytest.raises(OperationalRuleError) as captured:
        batch_support.invoke_root_rust_policy_batch([_ENVELOPE], operations=operations)

    error = captured.value
    assert error.__cause__ is failure
    assert "conftest produced no usable output" in str(error)
    assert expected_detail in str(error)
    assert process_calls == [
        (_expected_conftest_argv(scratch), True, True, 120.0, False)
    ]
    argv = process_calls[0][0]
    assert argv[argv.index("--namespace") + 1] == _ROOT_POLICY_NAMESPACE
    assert events == ["create", "write", "process", "cleanup"]
