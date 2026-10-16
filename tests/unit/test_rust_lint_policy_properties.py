"""Check root Rust lint policy inputs against an independent method oracle."""

from __future__ import annotations

import builtins
import io
import json
import os
import pathlib
import subprocess
import tempfile
import typing as typ

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from concordat.errors import OperationalRuleError
from tests.unit.rust_policy_batch_support import (
    _match_results_to_inputs,
    invoke_root_rust_policy_batch,
)
from tests.unit.rust_policy_property_support import (
    interpret_policy_results,
    method_arrays,
    method_policy_envelopes,
    oracle_accepts,
    policy_input_count,
    priority_policy_envelopes,
    required_methods,
)

if typ.TYPE_CHECKING:
    import collections.abc as cabc

    import pytest_mock

    from concordat.rules.runner import _ConftestResult

pytestmark = pytest.mark.timeout(120)
_OMIT: typ.Final = object()


def _conftest_result(**updates: object) -> dict[str, object]:
    """Build one valid result row with explicit field overrides or omissions."""
    result: dict[str, object] = {
        "filename": "envelope-0.json",
        "namespace": "canon.rust.lints",
        "successes": 0,
    }
    for key, value in updates.items():
        if value is _OMIT:
            result.pop(key, None)
        else:
            result[key] = value
    return result


@settings(max_examples=12, deadline=None)
@given(methods=st.lists(method_arrays(), min_size=1, max_size=policy_input_count()))
def test_rust_policy_matches_oracle_for_bounded_method_batches(
    methods: list[list[object]],
) -> None:
    """One explicit Conftest batch per bounded list matches the Python oracle."""
    envelopes = method_policy_envelopes(methods)
    results = invoke_root_rust_policy_batch(envelopes)
    actual = interpret_policy_results(results)
    expected = [oracle_accepts(entries) for entries in methods]
    assert actual == expected, list(zip(methods, expected, actual, strict=True))


def test_policy_envelope_helpers_do_not_access_files_or_start_processes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pure construction and interpretation stay outside the execution boundary."""

    def forbidden_boundary(*_args: object, **_kwargs: object) -> typ.Never:
        """Reject accidental file or process access by a pure helper."""
        msg = "pure policy helpers crossed an I/O boundary"
        raise AssertionError(msg)

    boundaries = (
        (builtins, "open"),
        (io, "open"),
        (os, "open"),
        (os, "system"),
        (pathlib.Path, "open"),
        (pathlib.Path, "read_text"),
        (pathlib.Path, "write_text"),
        (tempfile, "TemporaryDirectory"),
        (subprocess, "run"),
        (subprocess, "Popen"),
    )
    with monkeypatch.context() as patch:
        for owner, name in boundaries:
            patch.setattr(owner, name, forbidden_boundary)
        method_envelopes = method_policy_envelopes([required_methods()])
        priority_envelopes = priority_policy_envelopes([-1])
        result_rows = typ.cast(
            "list[list[_ConftestResult]]",
            [
                [_conftest_result(filename="envelope-0.json")],
                [
                    _conftest_result(
                        filename="envelope-1.json",
                        failures=[{"msg": "denied"}],
                    )
                ],
            ],
        )
        interpreted = interpret_policy_results(result_rows)

    assert len(method_envelopes) == 1
    assert len(priority_envelopes) == 1
    assert interpreted == [True, False]


def _valid_method_policy_cases(
    canonical: list[dict[str, str]],
) -> list[tuple[str, cabc.Sequence[object], bool]]:
    """Return the canonical and extensible positive cases."""
    return [
        ("canonical_pass", canonical, True),
        (
            "permuted_valid_extensions",
            [
                *reversed(canonical),
                "std::custom::ambient_lookup",
                {
                    "path": "std::custom::clock",
                    "reason": "use an injected clock",
                    "replacement": "test_support::Clock::now",
                    "allow-invalid": True,
                },
            ],
            True,
        ),
    ]


def _invalid_method_policy_cases(
    canonical: list[dict[str, str]],
    altered_reason: list[dict[str, str]],
    duplicate_path: list[object],
) -> list[tuple[str, cabc.Sequence[object], bool]]:
    """Return named negative cases for method paths and object fields."""
    return [
        ("missing_required_pair", canonical[1:], False),
        ("altered_required_reason", altered_reason, False),
        ("duplicate_bare_object_path", duplicate_path, False),
        (
            "unknown_field",
            [*canonical, {"path": "std::custom::lookup", "extra": True}],
            False,
        ),
        ("missing_path", [*canonical, {"reason": "missing path"}], False),
        ("null_path", [*canonical, {"path": None}], False),
        ("numeric_path", [*canonical, {"path": 17}], False),
        ("empty_path", [*canonical, {"path": ""}], False),
        (
            "numeric_reason",
            [*canonical, {"path": "std::custom::lookup", "reason": 9}],
            False,
        ),
        (
            "non_string_replacement",
            [
                *canonical,
                {"path": "std::custom::lookup", "replacement": ["invalid"]},
            ],
            False,
        ),
        (
            "non_boolean_allow_invalid",
            [
                *canonical,
                {"path": "std::custom::lookup", "allow-invalid": "true"},
            ],
            False,
        ),
        (
            "null_reason",
            [*canonical, {"path": "std::custom::lookup", "reason": None}],
            False,
        ),
        (
            "null_replacement",
            [*canonical, {"path": "std::custom::lookup", "replacement": None}],
            False,
        ),
        (
            "null_allow_invalid",
            [*canonical, {"path": "std::custom::lookup", "allow-invalid": None}],
            False,
        ),
    ]


def _method_policy_cases() -> list[tuple[str, cabc.Sequence[object], bool]]:
    """Return all named accepted and rejected method-array examples."""
    canonical = required_methods()
    altered_reason = [dict(method) for method in canonical]
    altered_reason[0]["reason"] = "use a different environment seam"
    duplicate_path = [*canonical, "std::env::var"]
    return _valid_method_policy_cases(canonical) + _invalid_method_policy_cases(
        canonical, altered_reason, duplicate_path
    )


def test_rust_policy_accepts_canonical_methods_and_rejects_mutations() -> None:
    """Named boundary examples keep positive and negative coverage explicit."""
    cases = _method_policy_cases()
    envelopes = method_policy_envelopes([methods for _, methods, _ in cases])
    results = invoke_root_rust_policy_batch(envelopes)
    actual = interpret_policy_results(results)
    expected = [accepted for _, _, accepted in cases]
    case_names = [name for name, _, _ in cases]
    assert actual == expected, list(zip(case_names, expected, actual, strict=True))
    assert [oracle_accepts(methods) for _, methods, _ in cases] == expected


def test_root_policy_batch_matches_reordered_results_by_filename() -> None:
    """Reordered Conftest output remains associated with its input case."""
    reordered = [
        _conftest_result(
            filename="/scratch/envelope-1.json", failures=[{"msg": "case b"}]
        ),
        _conftest_result(
            filename="/scratch/envelope-0.json", failures=[{"msg": "case a"}]
        ),
    ]
    matched = _match_results_to_inputs(
        typ.cast("list[_ConftestResult]", reordered),
        ["envelope-0.json", "envelope-1.json"],
    )
    messages = [
        [failure.get("msg") for failure in batch[0].get("failures", [])]
        for batch in matched
    ]
    assert messages == [["case a"], ["case b"]]


def test_root_policy_batch_accepts_official_clean_shape_without_failures(
    mocker: pytest_mock.MockerFixture,
) -> None:
    """A clean Conftest result omits its optional failures field."""
    clean_result = _conftest_result(filename="envelope.json")
    completed: subprocess.CompletedProcess[str] = subprocess.CompletedProcess(
        args=["conftest"],
        returncode=0,
        stdout=json.dumps([clean_result]),
        stderr="",
    )
    mocker.patch(
        "tests.unit.rust_policy_batch_support.subprocess.run",
        return_value=completed,
    )

    envelopes = method_policy_envelopes([required_methods()])
    results = invoke_root_rust_policy_batch(envelopes)
    assert interpret_policy_results(results) == [True]


@pytest.mark.parametrize(
    "results",
    [
        pytest.param([_conftest_result()], id="omitted-result"),
        pytest.param(
            [_conftest_result(), _conftest_result()],
            id="duplicate-result",
        ),
        pytest.param(
            [_conftest_result(filename=_OMIT)],
            id="malformed-result-filename",
        ),
        pytest.param(
            [_conftest_result(filename="unexpected.json")], id="unexpected-result"
        ),
    ],
)
def test_root_policy_batch_rejects_incomplete_or_ambiguous_output(
    results: list[dict[str, object]],
) -> None:
    """Every batch input needs exactly one result with a recognized filename."""
    with pytest.raises(
        OperationalRuleError, match="conftest produced no usable output"
    ):
        _match_results_to_inputs(
            typ.cast("list[_ConftestResult]", results),
            ["envelope-0.json", "envelope-1.json"],
        )


@pytest.mark.parametrize(
    "result",
    [
        pytest.param(_conftest_result(namespace=_OMIT), id="missing-namespace"),
        pytest.param(
            _conftest_result(namespace="canon.rust.other"), id="wrong-namespace"
        ),
        pytest.param(_conftest_result(successes=_OMIT), id="missing-successes"),
        pytest.param(_conftest_result(successes="zero"), id="malformed-successes"),
        pytest.param(_conftest_result(successes=True), id="boolean-successes"),
        pytest.param(_conftest_result(successes=-1), id="negative-successes"),
        pytest.param(_conftest_result(failures="none"), id="failures-not-an-array"),
        pytest.param(_conftest_result(failures=[None]), id="failure-not-an-object"),
        pytest.param(_conftest_result(failures=[{}]), id="failure-message-missing"),
        pytest.param(
            _conftest_result(failures=[{"msg": 9}]),
            id="failure-message-not-string",
        ),
        pytest.param(
            _conftest_result(failures=[{"msg": ""}]),
            id="failure-message-empty",
        ),
    ],
)
def test_root_policy_batch_rejects_invalid_result_shape(
    result: dict[str, object],
) -> None:
    """Malformed or misrouted records cannot become clean policy results."""
    with pytest.raises(
        OperationalRuleError, match="conftest produced no usable output"
    ):
        _match_results_to_inputs(
            typ.cast("list[_ConftestResult]", [result]), ["envelope-0.json"]
        )


def test_root_policy_batch_accepts_conftest_policy_denial_exit_status(
    mocker: pytest_mock.MockFixture,
) -> None:
    """Conftest exit 1 carries a policy verdict and must retain its finding."""
    denied: subprocess.CompletedProcess[str] = subprocess.CompletedProcess(
        args=["conftest"],
        returncode=1,
        stdout=json.dumps([
            _conftest_result(filename="envelope.json", failures=[{"msg": "denied"}])
        ]),
        stderr="",
    )
    mocker.patch(
        "tests.unit.rust_policy_batch_support.subprocess.run",
        return_value=denied,
    )
    results = invoke_root_rust_policy_batch([{"cargo": {}, "clippy": {}}])
    assert results[0][0].get("failures", [])[0].get("msg") == "denied"


@pytest.mark.parametrize(
    ("stdout", "returncode", "error_pattern"),
    [
        pytest.param(
            '{"unexpected": true}',
            0,
            "expected a JSON array",
            id="malformed-result-document",
        ),
        pytest.param(
            json.dumps([]),
            0,
            r"no result for envelope\.json",
            id="empty-result-array",
        ),
        pytest.param(
            json.dumps([_conftest_result(filename="envelope.json")]),
            1,
            "exit status does not match reported policy failures",
            id="denial-status-without-policy-failures",
        ),
        pytest.param(
            json.dumps([
                _conftest_result(filename="envelope.json", failures=[{"msg": "denied"}])
            ]),
            0,
            "exit status does not match reported policy failures",
            id="success-status-with-policy-failures",
        ),
    ],
)
def test_root_policy_batch_rejects_unusable_process_results(
    mocker: pytest_mock.MockerFixture,
    stdout: str,
    returncode: int,
    error_pattern: str,
) -> None:
    """Malformed documents and status conflicts cannot satisfy a batch."""
    completed: subprocess.CompletedProcess[str] = subprocess.CompletedProcess(
        args=["conftest"], returncode=returncode, stdout=stdout, stderr=""
    )
    mocker.patch(
        "tests.unit.rust_policy_batch_support.subprocess.run",
        return_value=completed,
    )
    with pytest.raises(OperationalRuleError, match=error_pattern):
        invoke_root_rust_policy_batch([{"cargo": {}, "clippy": {}}])
