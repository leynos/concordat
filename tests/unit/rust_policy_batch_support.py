"""Batch root Rust-policy inputs through one Conftest process."""

from __future__ import annotations

import json
import pathlib
import subprocess
import tempfile
import typing as typ

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


def invoke_root_rust_policy_batch(
    envelopes: cabc.Sequence[dict[str, object]],
) -> list[list[_ConftestResult]]:
    """Evaluate root-policy envelopes once and retain each input's findings.

    Conftest can emit results in a different order from its input files. The
    filename therefore remains the case identity, and incomplete or ambiguous
    output fails closed instead of turning into a clean policy result.

    An ``OperationalRuleError`` reports empty inputs; Conftest that cannot run
    (including a missing executable or timeout); missing or ambiguous output;
    or an exit status inconsistent with reported policy findings. Shape and
    decoding errors begin ``conftest produced no usable output`` and include
    their reason and relevant detail. A non-policy exit code reports
    ``conftest exited <code> without evaluating the policy`` and may include
    more detail. Process lookup, timeout, and JSON-decoding failures retain
    their original cause.

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

    with tempfile.TemporaryDirectory(prefix="concordat-rust-policy-") as scratch:
        filenames, input_paths = _write_batch_envelopes(
            pathlib.Path(scratch), envelopes
        )
        completed = _run_conftest(input_paths)
        results = runner._decode_results(completed, _ROOT_POLICY_ID)
        matched_results = _match_results_to_inputs(results, filenames)
        _validate_policy_exit_status(completed, results)
        return matched_results


def _write_batch_envelopes(
    scratch_dir: pathlib.Path, envelopes: cabc.Sequence[dict[str, object]]
) -> tuple[list[str], list[pathlib.Path]]:
    """Write numbered envelopes and return their names and paths."""
    filenames = runner._envelope_file_names(len(envelopes))
    input_paths = [scratch_dir / filename for filename in filenames]
    for path, envelope in zip(input_paths, envelopes, strict=True):
        path.write_text(json.dumps(envelope), encoding="utf-8")
    return filenames, input_paths


def _run_conftest(
    input_paths: cabc.Sequence[pathlib.Path],
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
        return subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - fixed argv, no shell
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
