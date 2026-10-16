"""Batch root Rust-policy inputs through one Conftest process."""

from __future__ import annotations

import dataclasses
import json
import pathlib
import subprocess
import tempfile
import typing as typ
from contextlib import contextmanager

from concordat.errors import OperationalRuleError
from concordat.rules import runner

if typ.TYPE_CHECKING:
    import collections.abc as cabc

    from concordat.rules.runner import _ConftestResult

_ROOT_POLICY_ID: typ.Final = "rust-lint-policy"
_ROOT_POLICY_NAMESPACE: typ.Final = "canon.rust.lints"
_ROOT_POLICY_DIR: typ.Final = (
    pathlib.Path(__file__).resolve().parents[2]
    / "platform-standards"
    / "canon"
    / "policies"
    / "rust"
)
_CONFTEST_TIMEOUT: typ.Final = 120.0


class _TemporaryDirectoryResource(typ.Protocol):
    """Describe the cleanup surface needed by one batch directory."""

    name: str

    def cleanup(self) -> None:
        """Remove the temporary directory."""
        ...


class _CreateTemporaryDirectory(typ.Protocol):
    """Describe the temporary-directory factory used for a batch."""

    def __call__(self, *, prefix: str) -> _TemporaryDirectoryResource:
        """Create a temporary directory with the requested prefix."""
        ...


class _WriteText(typ.Protocol):
    """Describe writing one UTF-8 batch envelope."""

    def __call__(
        self,
        path: pathlib.Path,
        contents: str,
        /,
        *,
        encoding: str = "utf-8",
    ) -> int:
        """Write the contents and return the number of characters written."""
        ...


class _RunProcess(typ.Protocol):
    """Describe the fixed, captured Conftest process invocation."""

    def __call__(
        self,
        argv: cabc.Sequence[str],
        /,
        *,
        capture_output: typ.Literal[True] = True,
        text: typ.Literal[True] = True,
        timeout: float = 120.0,
        check: typ.Literal[False] = False,
    ) -> subprocess.CompletedProcess[str]:
        """Run one captured text process with the batch timeout."""
        ...


@dataclasses.dataclass(frozen=True, slots=True)
class _RootRustPolicyBatchOperations:
    """Bundle the private storage and process operations for one batch."""

    create_directory: _CreateTemporaryDirectory
    write_text: _WriteText
    run_process: _RunProcess


def invoke_root_rust_policy_batch(
    envelopes: cabc.Sequence[dict[str, object]],
    *,
    operations: _RootRustPolicyBatchOperations | None = None,
) -> list[list[_ConftestResult]]:
    """Evaluate root-policy envelopes once and retain each input's findings.

    Conftest can emit results in a different order from its input files. The
    filename therefore remains the case identity, and incomplete or ambiguous
    output fails closed instead of turning into a clean policy result.

    An ``OperationalRuleError`` reports empty inputs; failures to create, write,
    or clean up temporary storage; Conftest that cannot run (including a
    missing executable or timeout); missing or ambiguous output; or an exit
    status inconsistent with reported policy findings. Storage failures and
    process lookup, timeout, and JSON-decoding failures retain their original
    cause. Shape errors begin ``conftest produced no usable output`` and include
    their reason and relevant detail. A non-policy exit code reports
    ``conftest exited <code> without evaluating the policy`` and may include
    more detail.

    Returns
    -------
    list[list[_ConftestResult]]
        Policy findings grouped by input envelope in their original order.
    """
    if not envelopes:
        shape_error = runner._conftest_shape_error(
            "no Rust-policy inputs were provided", _ROOT_POLICY_ID, "0 inputs"
        )
        raise shape_error

    resolved_operations = (
        _RootRustPolicyBatchOperations(
            create_directory=tempfile.TemporaryDirectory,
            write_text=pathlib.Path.write_text,
            run_process=subprocess.run,
        )
        if operations is None
        else operations
    )
    with _root_policy_batch_directory(resolved_operations) as scratch:
        filenames, input_paths = _write_batch_envelopes(
            pathlib.Path(scratch), envelopes, resolved_operations
        )
        completed = _run_conftest(input_paths, resolved_operations)
        results = runner._decode_results(completed, _ROOT_POLICY_ID)
        matched_results = _match_results_to_inputs(results, filenames)
        _validate_policy_exit_status(completed, results)
        return matched_results


@contextmanager
def _root_policy_batch_directory(
    operations: _RootRustPolicyBatchOperations,
) -> cabc.Iterator[str]:
    """Create and clean temporary storage for one root-policy batch."""
    prefix = "concordat-rust-policy-"
    try:
        scratch = operations.create_directory(prefix=prefix)
    except OSError as error:
        message = "could not create temporary directory for root Rust-policy batch"
        raise OperationalRuleError(
            message,
            operation="create-batch-directory",
            tool="conftest",
            resource=prefix,
        ) from error

    try:
        yield scratch.name
    finally:
        try:
            scratch.cleanup()
        except OSError as error:
            message = "could not clean up root Rust-policy batch directory"
            raise OperationalRuleError(
                message,
                operation="cleanup-batch-directory",
                tool="conftest",
                resource=scratch.name,
            ) from error


def _write_batch_envelopes(
    scratch_dir: pathlib.Path,
    envelopes: cabc.Sequence[dict[str, object]],
    operations: _RootRustPolicyBatchOperations,
) -> tuple[list[str], list[pathlib.Path]]:
    """Write numbered envelopes and return their names and paths."""
    filenames = runner._envelope_file_names(len(envelopes))
    input_paths = [scratch_dir / filename for filename in filenames]
    for path, envelope in zip(input_paths, envelopes, strict=True):
        contents = json.dumps(envelope)
        try:
            operations.write_text(path, contents, encoding="utf-8")
        except OSError as error:
            message = "could not write root Rust-policy envelope"
            raise OperationalRuleError(
                message,
                operation="write-batch-envelope",
                tool="conftest",
                resource=path,
            ) from error
    return filenames, input_paths


def _run_conftest(
    input_paths: cabc.Sequence[pathlib.Path],
    operations: _RootRustPolicyBatchOperations,
) -> subprocess.CompletedProcess[str]:
    """Run the root policy once, retaining the established process contract."""
    argv = [
        "conftest",
        "test",
        "--policy",
        str(_ROOT_POLICY_DIR),
        "--namespace",
        _ROOT_POLICY_NAMESPACE,
        "--output",
        "json",
        *map(str, input_paths),
    ]
    try:
        return operations.run_process(
            argv,
            capture_output=True,
            text=True,
            timeout=_CONFTEST_TIMEOUT,
            check=False,
        )
    except FileNotFoundError as error:
        shape_error = runner._conftest_shape_error(
            "conftest was not found on PATH", _ROOT_POLICY_ID, str(error)
        )
        raise shape_error from error
    except subprocess.TimeoutExpired as error:
        shape_error = runner._conftest_shape_error(
            f"conftest timed out after {_CONFTEST_TIMEOUT:g}s",
            _ROOT_POLICY_ID,
            str(error),
        )
        raise shape_error from error


def _validate_policy_exit_status(
    completed: subprocess.CompletedProcess[str], results: list[_ConftestResult]
) -> None:
    """Require Conftest's exit code to agree with its reported findings."""
    has_policy_failures = any(result.get("failures", []) for result in results)
    if (completed.returncode == 1) != has_policy_failures:
        shape_error = runner._conftest_shape_error(
            "exit status does not match reported policy failures",
            _ROOT_POLICY_ID,
            f"exit status {completed.returncode}",
        )
        raise shape_error


def _match_results_to_inputs(
    results: list[_ConftestResult], filenames: list[str]
) -> list[list[_ConftestResult]]:
    """Validate batch rows and restore their input order."""
    grouped: dict[str, list[_ConftestResult]] = {}
    expected = set(filenames)
    detail = f"{len(results)} results"
    for index, result in enumerate(results):
        fields = _validated_result_fields(result, index, detail)
        filename = _validated_result_filename(fields, index, detail, expected)
        _record_result(grouped, filename, result, detail)
    return _ordered_result_groups(grouped, filenames, detail)


def _validated_result_fields(
    result: _ConftestResult, index: int, detail: str
) -> dict[str, object]:
    """Validate the policy result's required fields and optional failures."""
    if not isinstance(result, dict):
        shape_error = runner._conftest_shape_error(
            f"result[{index}] is {type(result).__name__}, not an object",
            _ROOT_POLICY_ID,
            detail,
        )
        raise shape_error
    fields = typ.cast("dict[str, object]", result)
    if fields.get("namespace") != _ROOT_POLICY_NAMESPACE:
        shape_error = runner._conftest_shape_error(
            f"result[{index}].namespace is not {_ROOT_POLICY_NAMESPACE!r}",
            _ROOT_POLICY_ID,
            detail,
        )
        raise shape_error
    _validate_success_count(fields.get("successes"), index, detail)
    _validate_failure_records(fields.get("failures", []), index, detail)
    return fields


def _validate_success_count(value: object, index: int, detail: str) -> None:
    """Reject malformed successful-rule counts."""
    if not _is_nonnegative_integer(value):
        shape_error = runner._conftest_shape_error(
            f"result[{index}].successes is not a non-negative integer",
            _ROOT_POLICY_ID,
            detail,
        )
        raise shape_error


def _is_nonnegative_integer(value: object) -> typ.TypeGuard[int]:
    """Recognize non-negative integers without accepting booleans."""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _validate_failure_records(value: object, index: int, detail: str) -> None:
    """Validate Conftest's optional failure list and each message."""
    if not isinstance(value, list):
        shape_error = runner._conftest_shape_error(
            f"result[{index}].failures is not an array", _ROOT_POLICY_ID, detail
        )
        raise shape_error
    failures = typ.cast("list[object]", value)
    for position, failure in enumerate(failures):
        _validate_failure_record(failure, index, position, detail)


def _validate_failure_record(
    failure: object, index: int, position: int, detail: str
) -> None:
    """Require each failure record to carry a non-empty message."""
    if not isinstance(failure, dict):
        shape_error = runner._conftest_shape_error(
            f"result[{index}].failures[{position}] is not an object",
            _ROOT_POLICY_ID,
            detail,
        )
        raise shape_error
    message = typ.cast("dict[str, object]", failure).get("msg")
    if not _is_nonempty_string(message):
        shape_error = runner._conftest_shape_error(
            f"result[{index}].failures[{position}].msg is not a non-empty string",
            _ROOT_POLICY_ID,
            detail,
        )
        raise shape_error


def _is_nonempty_string(value: object) -> typ.TypeGuard[str]:
    """Recognize non-empty string fields."""
    return isinstance(value, str) and bool(value)


def _validated_result_filename(
    fields: dict[str, object], index: int, detail: str, expected: set[str]
) -> str:
    """Validate the result filename and return its batch identity."""
    raw_filename = fields.get("filename")
    if not _is_nonempty_string(raw_filename):
        shape_error = runner._conftest_shape_error(
            f"result[{index}] has no usable filename", _ROOT_POLICY_ID, detail
        )
        raise shape_error
    filename = pathlib.Path(raw_filename).name
    if filename not in expected:
        shape_error = runner._conftest_shape_error(
            f"unexpected result filename {filename!r}", _ROOT_POLICY_ID, detail
        )
        raise shape_error
    return filename


def _record_result(
    grouped: dict[str, list[_ConftestResult]],
    filename: str,
    result: _ConftestResult,
    detail: str,
) -> None:
    """Record one result, refusing duplicate filenames."""
    if filename in grouped:
        shape_error = runner._conftest_shape_error(
            f"duplicate result for {filename}", _ROOT_POLICY_ID, detail
        )
        raise shape_error
    grouped[filename] = [result]


def _ordered_result_groups(
    grouped: dict[str, list[_ConftestResult]], filenames: list[str], detail: str
) -> list[list[_ConftestResult]]:
    """Reject missing rows and return groups in their original order."""
    missing = [filename for filename in filenames if filename not in grouped]
    if missing:
        shape_error = runner._conftest_shape_error(
            f"no result for {', '.join(missing)}", _ROOT_POLICY_ID, detail
        )
        raise shape_error
    return [grouped[filename] for filename in filenames]
