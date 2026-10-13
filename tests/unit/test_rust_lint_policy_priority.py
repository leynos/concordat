"""Check Cargo's signed 8-bit Clippy lint-priority contract."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from tests.unit.rust_policy_property_support import (
    oracle_accepts,
    policy_accepts_priority_batch,
    policy_input_count,
    priority_values,
    required_methods,
)

pytestmark = pytest.mark.timeout(120)


@settings(max_examples=12, deadline=None)
@given(
    priorities=st.lists(priority_values(), min_size=1, max_size=policy_input_count())
)
def test_rust_policy_priority_matches_numeric_oracle_for_bounded_batches(
    priorities: list[object],
) -> None:
    """Normalized JSON priorities follow Cargo's i8-aware oracle."""
    actual = policy_accepts_priority_batch(priorities)
    expected = [oracle_accepts(required_methods(), priority) for priority in priorities]
    assert actual == expected, list(zip(priorities, expected, actual, strict=True))


@pytest.mark.parametrize(
    ("priority", "expected"),
    [
        pytest.param(-1, True, id="integer-minus-one"),
        pytest.param(0.0, True, id="decimal-0-0"),
        pytest.param(1e0, True, id="exponent-1e0"),
        pytest.param(-128, True, id="i8-minimum"),
        pytest.param(127, True, id="i8-maximum"),
        pytest.param(-128.0, True, id="integral-float-i8-minimum"),
        pytest.param(127.0, True, id="integral-float-i8-maximum"),
        pytest.param(-129, False, id="below-i8-minimum"),
        pytest.param(128, False, id="above-i8-maximum"),
        pytest.param(10**100, False, id="integer-outside-i8-domain"),
        pytest.param(1.5, False, id="fractional-number"),
        pytest.param(True, False, id="boolean-is-not-numeric"),
        pytest.param("-1", False, id="string-is-not-numeric"),
        pytest.param(None, False, id="null-priority"),
    ],
)
def test_rust_policy_accepts_only_in_range_integral_priorities(
    priority: object, *, expected: bool
) -> None:
    """Cargo 1.92 deserializes priorities as i8, rejecting integral overflow."""
    actual = policy_accepts_priority_batch([priority])
    assert actual == [expected]
    assert oracle_accepts(required_methods(), priority) is expected
