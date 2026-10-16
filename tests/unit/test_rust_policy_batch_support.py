"""Specify storage failures at the root Rust-policy batch boundary."""

from __future__ import annotations

import json
import pathlib
import subprocess
import typing as typ

import pytest

from concordat.errors import OperationalRuleError
from tests.unit import rust_policy_batch_support as batch_support

if typ.TYPE_CHECKING:
    import types

    import pytest_mock

_ENVELOPE: dict[str, object] = {"cargo": {}, "clippy": {}}


class _CleanupFailingTemporaryDirectory:
    """Model both temporary-directory cleanup interfaces with one failure."""

    def __init__(self, name: pathlib.Path, failure: OSError) -> None:
        """Retain the directory name and the cleanup error to raise."""
        self.name = str(name)
        self._failure = failure

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
        """Raise the configured cleanup error."""
        raise self._failure


def test_root_policy_batch_reports_directory_creation_failure(
    mocker: pytest_mock.MockerFixture,
) -> None:
    """Report directory-creation failure before invoking Conftest."""
    failure = OSError("temporary storage is unavailable")
    create_directory = mocker.patch.object(
        batch_support.tempfile,
        "TemporaryDirectory",
        side_effect=failure,
    )
    run_conftest = mocker.patch.object(batch_support, "_run_conftest")

    with pytest.raises(
        OperationalRuleError,
        match="could not create temporary directory",
    ) as captured:
        batch_support.invoke_root_rust_policy_batch([_ENVELOPE])

    error = captured.value
    assert error.__cause__ is failure
    assert error.operation == "create-batch-directory"
    assert error.tool == "conftest"
    assert error.resource == "concordat-rust-policy-"
    create_directory.assert_called_once_with(prefix="concordat-rust-policy-")
    run_conftest.assert_not_called()


def test_root_policy_batch_reports_envelope_write_failure_before_conftest(
    mocker: pytest_mock.MockerFixture,
) -> None:
    """Report envelope-write failure before invoking Conftest."""
    failure = OSError("input storage is read-only")
    write_text = mocker.patch.object(
        pathlib.Path,
        "write_text",
        autospec=True,
        side_effect=failure,
    )
    run_conftest = mocker.patch.object(batch_support, "_run_conftest")

    with pytest.raises(
        OperationalRuleError,
        match="could not write root Rust-policy envelope",
    ) as captured:
        batch_support.invoke_root_rust_policy_batch([_ENVELOPE])

    error = captured.value
    assert error.__cause__ is failure
    assert error.operation == "write-batch-envelope"
    assert error.tool == "conftest"
    write_text.assert_called_once()
    write_call = write_text.call_args
    assert write_call is not None
    write_path = write_call.args[0]
    assert isinstance(write_path, pathlib.Path)
    assert error.resource == write_path
    assert write_path.name == "envelope.json"
    run_conftest.assert_not_called()


def test_root_policy_batch_reports_cleanup_failure_after_evaluation(
    tmp_path: pathlib.Path,
    mocker: pytest_mock.MockerFixture,
) -> None:
    """Report cleanup failure after successful batch evaluation."""
    failure = OSError("temporary storage cleanup failed")
    scratch = tmp_path / "rust-policy-batch"
    scratch.mkdir()
    temporary_directory = _CleanupFailingTemporaryDirectory(scratch, failure)
    create_directory = mocker.patch.object(
        batch_support.tempfile,
        "TemporaryDirectory",
        return_value=temporary_directory,
    )
    completed = subprocess.CompletedProcess(
        args=["conftest"],
        returncode=0,
        stdout=json.dumps([
            {
                "filename": "envelope.json",
                "namespace": "canon.rust.lints",
                "successes": 0,
            }
        ]),
        stderr="",
    )
    run_conftest = mocker.patch.object(
        batch_support, "_run_conftest", return_value=completed
    )

    with pytest.raises(
        OperationalRuleError,
        match="could not clean up root Rust-policy batch directory",
    ) as captured:
        batch_support.invoke_root_rust_policy_batch([_ENVELOPE])

    error = captured.value
    written_envelope = scratch / "envelope.json"
    assert error.__cause__ is failure
    assert error.operation == "cleanup-batch-directory"
    assert error.tool == "conftest"
    assert error.resource == str(scratch)
    create_directory.assert_called_once_with(prefix="concordat-rust-policy-")
    run_conftest.assert_called_once_with([written_envelope])
    assert written_envelope.read_text(encoding="utf-8") == json.dumps(_ENVELOPE)
