"""Check root Rust lint policy inputs against an independent method oracle."""

from __future__ import annotations

import json
import subprocess
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
    method_arrays,
    oracle_accepts,
    policy_accepts_batch,
    policy_input_count,
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
    """One Conftest invocation per bounded list must match the Python oracle."""
    actual = policy_accepts_batch(methods)
    expected = [oracle_accepts(entries) for entries in methods]
    assert actual == expected, list(zip(methods, expected, actual, strict=True))


def test_rust_policy_accepts_canonical_methods_and_rejects_mutations() -> None:
    """Named boundary examples keep positive and negative coverage explicit."""
    canonical = required_methods()
    altered_reason = [dict(method) for method in canonical]
    altered_reason[0]["reason"] = "use a different environment seam"
    duplicate_path = [*canonical, "std::env::var"]
    cases: list[tuple[str, cabc.Sequence[object], bool]] = [
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

    actual = policy_accepts_batch([methods for _, methods, _ in cases])
    expected = [accepted for _, _, accepted in cases]
    assert actual == expected, list(
        zip([name for name, _, _ in cases], expected, actual, strict=True)
    )
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

    assert policy_accepts_batch([required_methods()]) == [True]


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


def test_root_policy_batch_rejects_malformed_result_documents(
    mocker: pytest_mock.MockFixture,
) -> None:
    """JSON that is not a Conftest result array cannot be read as a verdict."""
    malformed: subprocess.CompletedProcess[str] = subprocess.CompletedProcess(
        args=["conftest"], returncode=0, stdout='{"unexpected": true}', stderr=""
    )
    mocker.patch(
        "tests.unit.rust_policy_batch_support.subprocess.run",
        return_value=malformed,
    )
    with pytest.raises(OperationalRuleError, match="expected a JSON array"):
        invoke_root_rust_policy_batch([{"cargo": {}, "clippy": {}}])


def test_root_policy_batch_rejects_empty_result_array(
    mocker: pytest_mock.MockerFixture,
) -> None:
    """An empty result array cannot satisfy the requested input batch."""
    empty: subprocess.CompletedProcess[str] = subprocess.CompletedProcess(
        args=["conftest"], returncode=0, stdout=json.dumps([]), stderr=""
    )
    mocker.patch(
        "tests.unit.rust_policy_batch_support.subprocess.run",
        return_value=empty,
    )

    with pytest.raises(OperationalRuleError, match=r"no result for envelope\.json"):
        invoke_root_rust_policy_batch([{"cargo": {}, "clippy": {}}])


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


def test_root_policy_batch_rejects_denial_status_without_policy_failures(
    mocker: pytest_mock.MockerFixture,
) -> None:
    """Conftest exit 1 cannot describe clean result rows."""
    denied_without_failures: subprocess.CompletedProcess[str] = (
        subprocess.CompletedProcess(
            args=["conftest"],
            returncode=1,
            stdout=json.dumps([_conftest_result(filename="envelope.json")]),
            stderr="",
        )
    )
    mocker.patch(
        "tests.unit.rust_policy_batch_support.subprocess.run",
        return_value=denied_without_failures,
    )

    with pytest.raises(
        OperationalRuleError,
        match="exit status does not match reported policy failures",
    ):
        invoke_root_rust_policy_batch([{"cargo": {}, "clippy": {}}])


def test_root_policy_batch_rejects_success_status_with_policy_failures(
    mocker: pytest_mock.MockerFixture,
) -> None:
    """Conftest exit 0 cannot accompany a policy failure."""
    successful_with_failures: subprocess.CompletedProcess[str] = (
        subprocess.CompletedProcess(
            args=["conftest"],
            returncode=0,
            stdout=json.dumps([
                _conftest_result(filename="envelope.json", failures=[{"msg": "denied"}])
            ]),
            stderr="",
        )
    )
    mocker.patch(
        "tests.unit.rust_policy_batch_support.subprocess.run",
        return_value=successful_with_failures,
    )

    with pytest.raises(
        OperationalRuleError,
        match="exit status does not match reported policy failures",
    ):
        invoke_root_rust_policy_batch([{"cargo": {}, "clippy": {}}])
