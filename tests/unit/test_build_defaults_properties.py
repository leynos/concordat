"""Property tests for the BD-008 Makefile clause of `rust-build-defaults`.

Each example writes a generated Makefile beside a compliant Cargo
configuration, builds the real envelope (through `makeutil`) and evaluates the
real policy (through Conftest), then compares the findings with an oracle that
is computed from the generated decisions alone. The oracle never reads the
Makefile back, so a defect in the policy's option, delegation or scoping
handling cannot be shared with it.

The fixture-based Rego tests pin chosen shapes; these cover the spellings of
`$(MAKE)` options, the ways a delegation becomes dynamic, and where a
Makefile-wide assignment applies.
"""

from __future__ import annotations

import dataclasses
import pathlib
import tempfile
import typing as typ

from hypothesis import given, settings
from hypothesis import strategies as st

from concordat.rules import runner
from concordat.rules.envelope import build_build_defaults_envelope

_RULE_ID: typ.Final = "rust-build-defaults"
_PARAMETERS: typ.Final[dict[str, object]] = {
    "exception_documents": ["docs/developers-guide.md"],
    "exception_keyword": "Cranelift",
}
_FIXTURE_REPO: typ.Final = (
    pathlib.Path(__file__).resolve().parents[2]
    / "platform-standards/canon/lint-rules/rust-build-defaults/fixtures/repos"
    / "gate-delegated"
)
_CHECKOUT_FILES: typ.Final = (
    "Cargo.toml",
    "rust-toolchain.toml",
    ".cargo/config.toml",
)
_THREADS: typ.Final = "-Zthreads=8"
_MOLD: typ.Final = "-Clink-arg=-fuse-ld=mold"
_STATIC_OPTIONS: typ.Final = (
    "",
    "-s ",
    "--no-print-directory ",
    "--jobs=2 ",
    "-k --no-print-directory ",
)
_DYNAMIC_DELEGATIONS: typ.Final = ("-f other.mk unit", "-C sub unit", "$(TARGET)")
_DYNAMIC_MESSAGE: typ.Final = (
    'the "test" target reaches a dynamic recursive Make invocation, '
    "so its recipes cannot be proven"
)

_MISSING_FLAG_MESSAGE: typ.Final = (
    '{where} sets RUSTFLAGS without "{flag}"; an assigned RUSTFLAGS replaces '
    "every rustflags source in the Cargo configuration, so the gate build "
    "loses it"
)

type Finding = tuple[str, str]


@dataclasses.dataclass(frozen=True)
class FlagChoice:
    """Which of the fast flags a generated `RUSTFLAGS` value carries."""

    has_threads: bool
    has_mold: bool

    def value(self) -> str:
        """Return the `RUSTFLAGS` value, always starting with `-D warnings`."""
        flags = ["-D warnings"]
        flags += [_THREADS] if self.has_threads else []
        flags += [_MOLD] if self.has_mold else []
        return " ".join(flags)

    def missing(self) -> set[str]:
        """Return the fast flags the value leaves out."""
        return {
            flag
            for flag, present in ((_THREADS, self.has_threads), (_MOLD, self.has_mold))
            if not present
        }


_FLAGS = st.builds(FlagChoice, has_threads=st.booleans(), has_mold=st.booleans())


def _evaluate(makefile: str) -> set[Finding]:
    """Return the BD-008 findings for a checkout carrying *makefile*."""
    with tempfile.TemporaryDirectory(prefix="bd008-property-") as scratch:
        root = pathlib.Path(scratch)
        for name in _CHECKOUT_FILES:
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((_FIXTURE_REPO / name).read_bytes())
        (root / "Makefile").write_text(makefile, encoding="utf-8")
        envelope = build_build_defaults_envelope(root, _PARAMETERS)
        results = runner._invoke_conftest(_RULE_ID, typ.cast("typ.Any", envelope))
    return {
        (finding.verdict, finding.message)
        for finding in runner._findings_from_results(results)
        if finding.rule_id == "BD-008"
    }


def _missing_flag_findings(where: str, missing: set[str]) -> set[Finding]:
    """Return the findings an assignment at *where* lacking *missing* earns."""
    return {
        ("noncompliant", _MISSING_FLAG_MESSAGE.format(where=where, flag=flag))
        for flag in missing
    }


@settings(max_examples=20, deadline=None)
@given(
    flags=_FLAGS,
    options=st.sampled_from(_STATIC_OPTIONS),
    is_delegated=st.booleans(),
)
def test_a_delegated_gate_recipe_is_judged_like_a_direct_one(
    flags: FlagChoice, options: str, *, is_delegated: bool
) -> None:
    """Every spelling of `$(MAKE)` options reaches the recipe it delegates to."""
    recipe = f'\tRUSTFLAGS="{flags.value()}" cargo test\n'
    if is_delegated:
        makefile = f"test:\n\t$(MAKE) {options}inner\n\ninner:\n{recipe}"
        where = "the inner recipe"
    else:
        makefile = f"test:\n{recipe}"
        where = "the test recipe"
    assert _evaluate(makefile) == _missing_flag_findings(where, flags.missing()), (
        makefile
    )


@settings(max_examples=12, deadline=None)
@given(flags=_FLAGS, delegation=st.sampled_from(_DYNAMIC_DELEGATIONS))
def test_a_delegation_that_cannot_be_followed_is_indeterminate(
    flags: FlagChoice, delegation: str
) -> None:
    """A computed or foreign-file delegation is never passed or failed."""
    makefile = (
        f"test:\n\t$(MAKE) {delegation}\n\n"
        f'unit:\n\tRUSTFLAGS="{flags.value()}" cargo test\n'
    )
    assert _evaluate(makefile) == {("indeterminate", _DYNAMIC_MESSAGE)}, makefile


@settings(max_examples=12, deadline=None)
@given(flags=_FLAGS, has_gate_target=st.booleans())
def test_a_makefile_wide_assignment_applies_only_where_a_gate_exists(
    flags: FlagChoice, *, has_gate_target: bool
) -> None:
    """A Makefile-wide `RUSTFLAGS` overrides a gate build only if one exists."""
    target = "test:\n\tcargo test\n" if has_gate_target else "bench:\n\tcargo bench\n"
    makefile = f"RUSTFLAGS := {flags.value()}\n\n{target}"
    expected = (
        _missing_flag_findings("the Makefile-wide assignment", flags.missing())
        if has_gate_target
        else set()
    )
    assert _evaluate(makefile) == expected, makefile
