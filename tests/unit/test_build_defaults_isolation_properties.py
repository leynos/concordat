"""Generated BD-003/BD-008 properties with independent semantic oracles."""

from __future__ import annotations

import dataclasses
import json
import pathlib
import shutil
import tempfile
import typing as typ

from hypothesis import given, settings
from hypothesis import strategies as st

from concordat.rules import runner
from concordat.rules.envelope import (
    BuildDefaultsEnvelope,
    build_build_defaults_envelope,
)
from tests.unit.conftest_batch_support import invoke_conftest_batch

if typ.TYPE_CHECKING:
    import collections.abc as cabc

_RULE_ID: typ.Final = "rust-build-defaults"
_RULE_ROOT: typ.Final = (
    pathlib.Path(__file__).resolve().parents[2]
    / "platform-standards/canon/lint-rules/rust-build-defaults/fixtures/repos"
)
_BACKEND_FIXTURE: typ.Final = _RULE_ROOT / "compliant-scoped-rustflags-backend"
_WHITAKER_FIXTURE: typ.Final = _RULE_ROOT / "gate-isolated-whitaker"

_THREADS: typ.Final = "-Zthreads=8"
_CRANELIFT: typ.Final = "-Zcodegen-backend=cranelift"
_MOLD: typ.Final = "-Clink-arg=-fuse-ld=mold"
_TARGETS: typ.Final = (
    "x86_64-unknown-linux-gnu",
    "aarch64-unknown-linux-gnu",
    "x86_64-pc-windows-msvc",
    "aarch64-apple-darwin",
)
_LINUX_TARGETS: typ.Final = frozenset(_TARGETS[:2])
_TARGET_INDEXES: typ.Final = tuple(range(len(_TARGETS)))

type AssignmentScope = typ.Literal["recipe", "target", "inherited"]
type IsolationOutcome = typ.Literal["safe", "contaminated", "unproved"]
_ASSIGNMENT_SCOPES: typ.Final[tuple[AssignmentScope, ...]] = (
    "recipe",
    "target",
    "inherited",
)
_ISOLATION_OUTCOMES: typ.Final[tuple[IsolationOutcome, ...]] = (
    "safe",
    "contaminated",
    "unproved",
)


@dataclasses.dataclass(frozen=True)
class BackendCase:
    """The independent backend choices for one Cargo configuration."""

    is_build_backend: bool
    target_names: tuple[str, ...]
    source_order: tuple[int, ...]
    target_backend_mask: int

    def target_has_backend(self, index: int) -> bool:
        """Return whether this target's generated flags name Cranelift."""
        return bool(self.target_backend_mask & (1 << index))


@dataclasses.dataclass(frozen=True)
class WhitakerCase:
    """One direct installer-managed suite route and its known flag value."""

    assignment_scope: AssignmentScope
    outcome: IsolationOutcome
    rustflags: str
    command: str
    harmless_segments: bool


@dataclasses.dataclass(frozen=True)
class WrapperCase:
    """One non-direct command that remains subject to gate flag retention."""

    assignment_scope: AssignmentScope
    missing_flag: str | None
    command: str
    harmless_segments: bool


def _backend_case_strategy(*, is_build_backend: bool) -> st.SearchStrategy[BackendCase]:
    """Generate target names and ordering independently of backend scope."""
    mask = (
        st.integers(min_value=0, max_value=15)
        if is_build_backend
        else st.integers(min_value=1, max_value=15)
    )
    return st.builds(
        BackendCase,
        is_build_backend=st.just(is_build_backend),
        target_names=st.permutations(_TARGETS),
        source_order=st.permutations(_TARGET_INDEXES),
        target_backend_mask=mask,
    )


_BACKEND_BATCH: typ.Final = st.tuples(
    *(_backend_case_strategy(is_build_backend=True) for _ in range(4)),
    *(_backend_case_strategy(is_build_backend=False) for _ in range(4)),
)


def _direct_case_strategy(
    assignment_scope: AssignmentScope, outcome: IsolationOutcome
) -> st.SearchStrategy[WhitakerCase]:
    """Vary command spelling while holding the selected policy outcome."""
    values: st.SearchStrategy[str]
    if outcome == "safe":
        values = st.just("-D warnings")
    elif outcome == "contaminated":
        values = st.sampled_from([
            f"-D warnings {_THREADS}",
            "-D warnings -Z threads=8",
            f"-D warnings {_CRANELIFT}",
            "-D warnings -Z codegen-backend=cranelift",
            f"-D warnings {_MOLD}",
        ])
    else:
        values = st.sampled_from([
            "$(UNRESOLVED_FLAGS)",
            "-D warnings $(UNRESOLVED_FLAGS)",
            "$(FLAGS_NOT_SET_BY_MAKE)",
        ])
    return st.builds(
        WhitakerCase,
        assignment_scope=st.just(assignment_scope),
        outcome=st.just(outcome),
        rustflags=values,
        command=st.sampled_from([
            "whitaker --all",
            "env MODE=ci /opt/bin/whitaker --all",
            "MODE=ci /usr/local/bin/whitaker.exe --all",
        ]),
        harmless_segments=(
            st.booleans() if assignment_scope == "recipe" else st.just(value=False)
        ),
    )


_DIRECT_BATCH: typ.Final = st.tuples(
    *(
        _direct_case_strategy(scope, outcome)
        for scope in _ASSIGNMENT_SCOPES
        for outcome in _ISOLATION_OUTCOMES
    )
)


def _prefix_rustflags(command: str, rustflags: str) -> str:
    """Place recipe-scope flags where the direct executable parser accepts them."""
    if command.startswith("env "):
        env_command = command.removeprefix("env ")
        return f'env RUSTFLAGS="{rustflags}" {env_command}'
    return f'RUSTFLAGS="{rustflags}" {command}'


def _wrapper_case_strategy(
    assignment_scope: AssignmentScope, missing_flag: str | None
) -> st.SearchStrategy[WrapperCase]:
    """Generate a wrapper whose flags are judged by ordinary gate rules."""
    return st.builds(
        WrapperCase,
        assignment_scope=st.just(assignment_scope),
        missing_flag=st.just(missing_flag),
        command=st.sampled_from([
            "python scripts/whitaker-wrapper.py --all",
            "cargo test -p whitaker",
        ]),
        harmless_segments=st.booleans(),
    )


_REQUIRED_FLAGS: typ.Final = (_THREADS, _MOLD)
_WRAPPER_BATCH: typ.Final = st.tuples(
    *(
        _wrapper_case_strategy(scope, missing_flag)
        for scope in _ASSIGNMENT_SCOPES
        for missing_flag in (None, *_REQUIRED_FLAGS)
    )
)


def _backend_config(case: BackendCase) -> str:
    """Render a valid Cargo config from semantic backend choices."""
    build_flags = [_THREADS]
    if case.is_build_backend:
        build_flags.append(_CRANELIFT)
    lines = ["[build]", f"rustflags = {json.dumps(build_flags)}"]
    for index in case.source_order:
        target = case.target_names[index]
        flags = [_THREADS]
        if target in _LINUX_TARGETS:
            flags.append(_MOLD)
        if case.target_has_backend(index):
            flags.append(_CRANELIFT)
        lines.extend(["", f"[target.{target}]", f"rustflags = {json.dumps(flags)}"])
    return "\n".join(lines) + "\n"


def _render_whitaker_makefile(case: WhitakerCase) -> str:
    """Render only the supported direct target and assignment shapes."""
    command = case.command
    if case.assignment_scope == "recipe":
        command = _prefix_rustflags(command, case.rustflags)
    if case.harmless_segments:
        command = f"printf 'starting'; {command}; printf 'finished'"
    if case.assignment_scope == "recipe":
        return f"lint:\n\t{command}\n"
    if case.assignment_scope == "target":
        return (
            "lint: suite\n\n"
            f"suite: export RUSTFLAGS := {case.rustflags}\n"
            f"suite:\n\t{command}\n"
        )
    return (
        f"lint: export RUSTFLAGS := {case.rustflags}\n"
        "lint: suite\n\n"
        f"suite:\n\t{command}\n"
    )


def _render_wrapper_makefile(case: WrapperCase) -> str:
    """Render one unknown wrapper with the generated ordinary-gate flags."""
    retained = [flag for flag in _REQUIRED_FLAGS if flag != case.missing_flag]
    value = " ".join(["-D warnings", *retained])
    command = case.command
    if case.assignment_scope == "recipe":
        command = _prefix_rustflags(command, value)
    if case.harmless_segments:
        command = f"printf 'starting'; {command}; printf 'finished'"
    if case.assignment_scope == "recipe":
        return f"lint:\n\t{command}\n"
    if case.assignment_scope == "target":
        return (
            f"lint: suite\n\nsuite: export RUSTFLAGS := {value}\nsuite:\n\t{command}\n"
        )
    return f"lint: export RUSTFLAGS := {value}\nlint: suite\n\nsuite:\n\t{command}\n"


def _build_envelopes(
    fixture: pathlib.Path,
    *,
    cargo_configs: cabc.Sequence[str | None],
    makefiles: cabc.Sequence[str | None],
) -> list[BuildDefaultsEnvelope]:
    """Build isolated real envelopes for a bounded Conftest batch."""
    if len(cargo_configs) != len(makefiles):
        message = "each generated checkout needs one config and Makefile slot"
        raise ValueError(message)
    envelopes: list[BuildDefaultsEnvelope] = []
    with tempfile.TemporaryDirectory(
        prefix="rust-build-isolation-property-"
    ) as scratch:
        root = pathlib.Path(scratch)
        for index, (cargo_config, makefile) in enumerate(
            zip(cargo_configs, makefiles, strict=True)
        ):
            checkout = root / f"case-{index}"
            shutil.copytree(fixture, checkout)
            if cargo_config is not None:
                (checkout / ".cargo/config.toml").write_text(
                    cargo_config, encoding="utf-8"
                )
            if makefile is not None:
                (checkout / "Makefile").write_text(makefile, encoding="utf-8")
            envelopes.append(build_build_defaults_envelope(checkout))
    return envelopes


def _evaluate_batch(
    envelopes: cabc.Sequence[BuildDefaultsEnvelope],
) -> list[tuple[runner.Finding, ...]]:
    """Evaluate every generated envelope in one Conftest process."""
    batches = invoke_conftest_batch(
        _RULE_ID, typ.cast("cabc.Sequence[runner.RuleEnvelope]", envelopes)
    )
    return [runner._findings_from_results(results) for results in batches]


def _rule_findings(
    findings: tuple[runner.Finding, ...], rule_id: str
) -> tuple[runner.Finding, ...]:
    """Select findings from one clause without hiding unrelated results."""
    return tuple(finding for finding in findings if finding.rule_id == rule_id)


def _expected_missing_backend_targets(case: BackendCase) -> set[str]:
    """Derive BD-003 targets from generated semantic facts, not parsed input."""
    return {
        case.target_names[index]
        for index in _TARGET_INDEXES
        if case.is_build_backend and not case.target_has_backend(index)
    }


def _expected_direct_verdict(case: WhitakerCase) -> str | None:
    """Derive the direct-suite result from its known-value classification."""
    if case.outcome == "safe":
        return None
    return "noncompliant" if case.outcome == "contaminated" else "indeterminate"


@settings(max_examples=8, deadline=None)
@given(cases=_BACKEND_BATCH)
def test_only_build_wide_backends_must_survive_every_target_replacement(
    cases: tuple[BackendCase, ...],
) -> None:
    """Target-local Cranelift remains local across source names and order."""
    expected_by_case = [_expected_missing_backend_targets(case) for case in cases]
    envelopes = _build_envelopes(
        _BACKEND_FIXTURE,
        cargo_configs=[_backend_config(case) for case in cases],
        makefiles=[None] * len(cases),
    )
    findings = _evaluate_batch(envelopes)
    for case, result, expected_missing in zip(
        cases, findings, expected_by_case, strict=True
    ):
        backend_findings = _rule_findings(result, "BD-003")
        other_findings = tuple(f for f in result if f.rule_id != "BD-003")
        reported_missing = {
            target
            for finding in backend_findings
            for target in case.target_names
            if f'"target.{target}"' in finding.message
        }
        assert not other_findings, other_findings
        assert all(finding.verdict == "noncompliant" for finding in backend_findings)
        assert reported_missing == expected_missing, (case, backend_findings)


@settings(max_examples=8, deadline=None)
@given(cases=_DIRECT_BATCH)
def test_direct_whitaker_isolation_tracks_known_and_unproved_flags(
    cases: tuple[WhitakerCase, ...],
) -> None:
    """The exemption requires one known safe value at every assignment scope."""
    assert len(cases) == 9, "the direct isolation batch must cover all nine cases"
    expected_by_case = [_expected_direct_verdict(case) for case in cases]
    envelopes = _build_envelopes(
        _WHITAKER_FIXTURE,
        cargo_configs=[None] * len(cases),
        makefiles=[_render_whitaker_makefile(case) for case in cases],
    )
    findings = _evaluate_batch(envelopes)
    for case, result, expected_verdict in zip(
        cases, findings, expected_by_case, strict=True
    ):
        bd008 = _rule_findings(result, "BD-008")
        assert all(finding.rule_id == "BD-008" for finding in result), result
        if expected_verdict is None:
            assert bd008 == (), (case, bd008)
        else:
            assert {finding.verdict for finding in bd008} == {expected_verdict}, (
                case,
                bd008,
            )


@settings(max_examples=8, deadline=None)
@given(cases=_WRAPPER_BATCH)
def test_unknown_whitaker_wrappers_keep_ordinary_fast_flag_checks(
    cases: tuple[WrapperCase, ...],
) -> None:
    """A command wrapper gets no isolation exemption or special flag rules."""
    assert len(cases) == 9, "the wrapper batch must cover all nine cases"
    expected_missing_by_case = [case.missing_flag for case in cases]
    envelopes = _build_envelopes(
        _WHITAKER_FIXTURE,
        cargo_configs=[None] * len(cases),
        makefiles=[_render_wrapper_makefile(case) for case in cases],
    )
    findings = _evaluate_batch(envelopes)
    for case, result, expected_missing in zip(
        cases, findings, expected_missing_by_case, strict=True
    ):
        bd008 = _rule_findings(result, "BD-008")
        assert all(finding.rule_id == "BD-008" for finding in result), result
        if expected_missing is None:
            assert bd008 == (), (case, bd008)
        else:
            assert len(bd008) == 1, (case, bd008)
            assert bd008[0].verdict == "noncompliant", bd008
            assert expected_missing in bd008[0].message, bd008[0]
