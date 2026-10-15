"""Keep the generated isolation batches non-empty and complete."""

from __future__ import annotations

from hypothesis import given

from tests.unit import test_build_defaults_isolation_properties as properties

_EXPECTED_SCOPES = ("recipe", "target", "inherited")
_EXPECTED_OUTCOMES = ("safe", "contaminated", "unproved")
_EXPECTED_WRAPPER_FLAGS = (
    None,
    "-Zthreads=8",
    "-Clink-arg=-fuse-ld=mold",
)


@given(
    backend_cases=properties._BACKEND_BATCH,
    direct_cases=properties._DIRECT_BATCH,
    wrapper_cases=properties._WRAPPER_BATCH,
)
def test_isolation_batches_draw_every_supported_case(
    backend_cases: tuple[properties.BackendCase, ...],
    direct_cases: tuple[properties.WhitakerCase, ...],
    wrapper_cases: tuple[properties.WrapperCase, ...],
) -> None:
    """Each generated batch contains its complete finite policy dimensions."""
    assert len(backend_cases) == 8
    assert sum(case.is_build_backend for case in backend_cases) == 4

    expected_direct = {
        (scope, outcome) for scope in _EXPECTED_SCOPES for outcome in _EXPECTED_OUTCOMES
    }
    assert len(direct_cases) == 9
    assert {
        (case.assignment_scope, case.outcome) for case in direct_cases
    } == expected_direct

    expected_wrappers = {
        (scope, missing_flag)
        for scope in _EXPECTED_SCOPES
        for missing_flag in _EXPECTED_WRAPPER_FLAGS
    }
    assert len(wrapper_cases) == 9
    assert {
        (case.assignment_scope, case.missing_flag) for case in wrapper_cases
    } == expected_wrappers
