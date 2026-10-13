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
        scratch_dir = pathlib.Path(scratch)
        filenames = runner._envelope_file_names(len(envelopes))
        input_paths = [scratch_dir / filename for filename in filenames]
        for path, envelope in zip(input_paths, envelopes, strict=True):
            path.write_text(json.dumps(envelope), encoding="utf-8")

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
            completed = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - fixed argv, no shell
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

        results = runner._decode_results(completed, _ROOT_POLICY_ID)
        matched_results = _match_results_to_inputs(results, filenames)
        has_policy_failures = any(result.get("failures", []) for result in results)
        if (completed.returncode == 1) != has_policy_failures:
            shape_error = runner._conftest_shape_error(
                "exit status does not match reported policy failures",
                _ROOT_POLICY_ID,
                f"exit status {completed.returncode}",
            )
            raise shape_error
        return matched_results


def _match_results_to_inputs(
    results: list[_ConftestResult], filenames: list[str]
) -> list[list[_ConftestResult]]:
    """Return validated results in input order, rejecting ambiguous output."""
    grouped: dict[str, list[_ConftestResult]] = {}
    expected = set(filenames)
    detail = f"{len(results)} results"
    for index, result in enumerate(results):
        if not isinstance(result, dict):
            shape_error = runner._conftest_shape_error(
                f"result[{index}] is {type(result).__name__}, not an object",
                _ROOT_POLICY_ID,
                detail,
            )
            raise shape_error
        fields = typ.cast("dict[str, object]", result)
        namespace = fields.get("namespace")
        if namespace != _ROOT_POLICY_NAMESPACE:
            shape_error = runner._conftest_shape_error(
                f"result[{index}].namespace is not {_ROOT_POLICY_NAMESPACE!r}",
                _ROOT_POLICY_ID,
                detail,
            )
            raise shape_error
        successes = fields.get("successes")
        if (
            not isinstance(successes, int)
            or isinstance(successes, bool)
            or successes < 0
        ):
            shape_error = runner._conftest_shape_error(
                f"result[{index}].successes is not a non-negative integer",
                _ROOT_POLICY_ID,
                detail,
            )
            raise shape_error

        # Conftest v0.52.0 tags this field `omitempty`, so absence is a clean
        # empty list only after its required result envelope is validated.
        failures = fields.get("failures", [])
        if not isinstance(failures, list):
            shape_error = runner._conftest_shape_error(
                f"result[{index}].failures is not an array",
                _ROOT_POLICY_ID,
                detail,
            )
            raise shape_error
        for position, failure in enumerate(failures):
            if not isinstance(failure, dict):
                shape_error = runner._conftest_shape_error(
                    f"result[{index}].failures[{position}] is not an object",
                    _ROOT_POLICY_ID,
                    detail,
                )
                raise shape_error
            message = typ.cast("dict[str, object]", failure).get("msg")
            if not isinstance(message, str) or not message:
                shape_error = runner._conftest_shape_error(
                    f"result[{index}].failures[{position}].msg is not "
                    "a non-empty string",
                    _ROOT_POLICY_ID,
                    detail,
                )
                raise shape_error

        raw_filename = fields.get("filename")
        if not isinstance(raw_filename, str) or not raw_filename:
            shape_error = runner._conftest_shape_error(
                f"result[{index}] has no usable filename",
                _ROOT_POLICY_ID,
                detail,
            )
            raise shape_error
        filename = pathlib.Path(raw_filename).name
        if filename not in expected:
            shape_error = runner._conftest_shape_error(
                f"unexpected result filename {filename!r}",
                _ROOT_POLICY_ID,
                detail,
            )
            raise shape_error
        if filename in grouped:
            shape_error = runner._conftest_shape_error(
                f"duplicate result for {filename}",
                _ROOT_POLICY_ID,
                detail,
            )
            raise shape_error
        grouped[filename] = [result]

    missing = [filename for filename in filenames if filename not in grouped]
    if missing:
        shape_error = runner._conftest_shape_error(
            f"no result for {', '.join(missing)}",
            _ROOT_POLICY_ID,
            detail,
        )
        raise shape_error
    return [grouped[filename] for filename in filenames]
