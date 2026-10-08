"""Property test of the BD-009 setup-action ordering invariant.

The policy takes the `RUSTFLAGS` a later step inherits from the latest
setup-rust step that precedes it. Each example writes a workflow whose job
interleaves setup-rust steps (varying `rustflags` and `toolchain`) around one
cargo gate step, builds the real envelope and evaluates the real policy
through the public `runner.run_rule`, then compares the findings with an
oracle computed from the generated steps alone. A setup step after the gate
step, or an earlier one the latest replaces, must never decide the verdict.
"""

from __future__ import annotations

import dataclasses
import pathlib
import re
import tempfile
import typing as typ

from hypothesis import given, settings
from hypothesis import strategies as st

from concordat.rules import runner
from concordat.rules.envelope import build_build_defaults_envelope

_RULE_ID: typ.Final = "rust-build-defaults"
_FIXTURE_REPO: typ.Final = (
    pathlib.Path(__file__).resolve().parents[2]
    / "platform-standards/canon/lint-rules/rust-build-defaults/fixtures/repos"
    / "workflow-setup-rust"
)
_CHECKOUT_FILES: typ.Final = ("Cargo.toml", "rust-toolchain.toml")
_SETUP_USES: typ.Final = (
    "leynos/shared-actions/.github/actions/setup-rust"
    "@0123456789abcdef0123456789abcdef01234567"
)
_THREADS: typ.Final = "-Zthreads=8"
_MOLD: typ.Final = "-Clink-arg=-fuse-ld=mold"
# `None` leaves the input unset, so the action's own default applies.
_RUSTFLAGS_INPUTS: typ.Final = (
    None,
    "",
    "-D warnings",
    f"-D warnings {_THREADS}",
    f"-D warnings {_MOLD}",
    f"-D warnings {_THREADS} {_MOLD}",
)
_DEFAULT_RUSTFLAGS: typ.Final = "-D warnings"
_TOOLCHAIN_INPUTS: typ.Final = (None, "stable", "nightly-2026-05-28")
_LACKS: typ.Final = re.compile(r'which lacks "([^"]+)"')


@dataclasses.dataclass(frozen=True)
class SetupStep:
    """A setup-rust step and the inputs it is given."""

    rustflags: str | None
    toolchain: str | None

    def render(self) -> str:
        """Return the step as workflow YAML."""
        inputs = []
        if self.rustflags is not None:
            inputs.append(f"          rustflags: '{self.rustflags}'\n")
        if self.toolchain is not None:
            inputs.append(f"          toolchain: {self.toolchain}\n")
        with_block = f"        with:\n{''.join(inputs)}" if inputs else ""
        return f"      - uses: {_SETUP_USES}\n{with_block}"

    def exported_flags(self) -> str:
        """Return the `RUSTFLAGS` this step exports to later steps."""
        return _DEFAULT_RUSTFLAGS if self.rustflags is None else self.rustflags


_SETUP_STEPS = st.builds(
    SetupStep,
    rustflags=st.sampled_from(_RUSTFLAGS_INPUTS),
    toolchain=st.sampled_from(_TOOLCHAIN_INPUTS),
)


def _expected_missing(before: list[SetupStep]) -> set[str]:
    """Return the flags the gate step lacks, judged from the latest setup step."""
    if not before or not before[-1].exported_flags():
        return set()
    latest = before[-1]
    flags = latest.exported_flags()
    required = {_MOLD}  # the runner is a literal Linux label
    if latest.toolchain in {None, "nightly-2026-05-28"}:
        required.add(_THREADS)
    return {flag for flag in required if flag not in flags}


def _missing_flags(workflow: str) -> set[str]:
    """Return the flags BD-009 reports a gate step lacking in *workflow*."""
    with tempfile.TemporaryDirectory(prefix="bd009-property-") as scratch:
        root = pathlib.Path(scratch)
        for name in _CHECKOUT_FILES:
            (root / name).write_bytes((_FIXTURE_REPO / name).read_bytes())
        target = root / ".github/workflows/ci.yml"
        target.parent.mkdir(parents=True)
        target.write_text(workflow, encoding="utf-8")
        envelope = build_build_defaults_envelope(root)
        result = runner.run_rule(
            _RULE_ID,
            root,
            envelope_builder=lambda _rule_id, _checkout: typ.cast(
                "runner.RuleEnvelope", envelope
            ),
        )
    missing: set[str] = set()
    for finding in result.findings:
        if finding.rule_id != "BD-009":
            continue
        assert finding.verdict == "noncompliant", (finding, workflow)
        found = _LACKS.search(finding.message)
        assert found is not None, finding.message
        missing.add(found.group(1))
    return missing


@settings(max_examples=40, deadline=None)
@given(
    before=st.lists(_SETUP_STEPS, max_size=3),
    after=st.lists(_SETUP_STEPS, max_size=3),
)
def test_only_the_latest_preceding_setup_step_supplies_the_flags(
    before: list[SetupStep], after: list[SetupStep]
) -> None:
    """A gate step inherits from the latest setup step before it, no other."""
    steps = [
        *(step.render() for step in before),
        "      - name: Test\n        run: cargo nextest run --workspace\n",
        *(step.render() for step in after),
    ]
    workflow = (
        "on: pull_request\njobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n"
        + "".join(steps)
    )
    assert _missing_flags(workflow) == _expected_missing(before), workflow
