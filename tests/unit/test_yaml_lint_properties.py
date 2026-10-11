"""Property tests for the lint-gate and CI clauses of `yaml-lint-baseline`.

Each example writes a generated Makefile and workflow beside the compliant
configuration files, builds the real envelope (through `makeutil`) and
evaluates the real policy (through Conftest, by the public `runner.run_rule`),
then compares which rules found something with an oracle computed from the
generated decisions alone. The oracle never reads the generated text back, so a
defect in the policy's closure, flag or step-order handling cannot be shared
with it.

The fixture-based Rego tests pin chosen shapes and messages; these cover the
combinations: where on the `lint` path a tool sits, how it is invoked, which
flags override the estate configuration, and where the installer sits relative
to the lint step.
"""

from __future__ import annotations

import dataclasses
import pathlib
import tempfile
import typing as typ

from hypothesis import given, settings
from hypothesis import strategies as st

from concordat.rules import runner
from concordat.rules.yaml_lint_envelope import build_yaml_lint_envelope

_RULE_ID: typ.Final = "yaml-lint-baseline"
_FIXTURES: typ.Final = (
    pathlib.Path(__file__).resolve().parents[2]
    / "platform-standards/canon/lint-rules"
    / _RULE_ID
    / "fixtures"
)
_CANON: typ.Final = pathlib.Path(__file__).resolve().parents[2] / (
    "platform-standards/canon/lint"
)
_INSTALLER: typ.Final = (
    "leynos/shared-actions/.github/actions/install-yaml-lint@"
    "0123456789abcdef0123456789abcdef01234567"
)
_PIPE: typ.Final = "git ls-files -z '*.yml' '*.yaml' | xargs -0 -r"

# (text appended to the yamllint arguments, whether it overrides the config)
_YAMLLINT_FLAGS: typ.Final = (
    ("", False),
    (" --config-file .yamllint.yml", False),
    (" -c .yamllint.yml", False),
    (" -d relaxed", True),
    (" -drelaxed", True),
    (" --config-data=relaxed", True),
    (" -c relaxed.yml", True),
    (" -crelaxed.yml", True),
    (" --config-file=relaxed.yml", True),
    (" --no-warnings", True),
)
_STRICT_SPELLINGS: typ.Final = ("--strict", "-s", "")
_INVOCATIONS: typ.Final = (
    # (shell prefix of the make command, runs the gate)
    ("make lint", True),
    ("make -j4 check-fmt lint", True),
    ("CI=1 make lint", True),
    ("make -n lint", False),
    ("echo make lint", False),
    ("echo '; make lint'", False),
    ("make lint || true", False),
    ("make markdownlint", False),
)


@dataclasses.dataclass(frozen=True)
class GateChoice:
    """One generated shape for the `lint` target's yamllint recipe."""

    depth: int
    on_path: bool
    strict: str
    git_fed: bool
    flags: tuple[str, bool]
    soft: bool

    def provides_yamllint(self) -> bool:
        """Return whether the policy should accept this yamllint recipe."""
        return (
            self.on_path
            and self.strict != ""
            and self.git_fed
            and not self.flags[1]
            and not self.soft
        )

    def makefile(self) -> str:
        """Render a Makefile whose `lint` closure holds this recipe."""
        chain = [f"stage{index}" for index in range(self.depth)]
        entry = chain[0] if chain else "yamllint"
        targets = ["actionlint", entry] if self.on_path else ["actionlint"]
        names = [*chain, "yamllint"]
        lines = [
            "YAMLLINT ?= yamllint",
            "ACTIONLINT ?= actionlint",
            f".PHONY: lint actionlint yamllint {' '.join(chain)}",
            f"lint: {' '.join(targets)}",
            "",
        ]
        for index, name in enumerate(chain):
            following = names[index + 1]
            lines += [f"{name}: {following}", ""]
        feeder = _PIPE if self.git_fed else "true &&"
        arguments = " ".join(
            part for part in (self.strict, self.flags[0].strip(), "--") if part
        )
        prefix = "-" if self.soft else ""
        lines += [
            "yamllint:",
            f"\t{prefix}{feeder} $(YAMLLINT) {arguments}",
            "",
            "actionlint:",
            "\t$(ACTIONLINT)",
        ]
        return "\n".join(lines) + "\n"


@dataclasses.dataclass(frozen=True)
class CiChoice:
    """One generated shape for the workflow's lint job."""

    installer_before: bool
    installer_present: bool
    invocation: tuple[str, bool]
    continue_on_error: bool

    def provides_lint(self) -> bool:
        """Return whether the policy should accept this workflow."""
        return (
            self.installer_present
            and self.installer_before
            and self.invocation[1]
            and not self.continue_on_error
        )

    def workflow(self) -> str:
        """Render the workflow."""
        installer = f"      - uses: {_INSTALLER}\n"
        lint = f"      - run: {self.invocation[0]}\n"
        if self.continue_on_error:
            lint += "        continue-on-error: true\n"
        steps = lint
        if self.installer_present:
            steps = installer + lint if self.installer_before else lint + installer
        return (
            "name: CI\non: [push]\njobs:\n  lint:\n    runs-on: ubuntu-latest\n"
            f"    steps:\n{steps}"
        )


_GATES = st.builds(
    GateChoice,
    depth=st.integers(min_value=0, max_value=3),
    on_path=st.booleans(),
    strict=st.sampled_from(_STRICT_SPELLINGS),
    git_fed=st.booleans(),
    flags=st.sampled_from(_YAMLLINT_FLAGS),
    soft=st.booleans(),
)
_CI = st.builds(
    CiChoice,
    installer_before=st.booleans(),
    installer_present=st.booleans(),
    invocation=st.sampled_from(_INVOCATIONS),
    continue_on_error=st.booleans(),
)


def _evaluate(makefile: str, workflow: str) -> set[str]:
    """Return the rule ids that found something in a checkout of the two files."""
    with tempfile.TemporaryDirectory(prefix="yl-property-") as scratch:
        root = pathlib.Path(scratch)
        (root / "Makefile").write_text(makefile, encoding="utf-8")
        (root / ".yamllint-estate.yml").write_bytes(
            (_CANON / "yaml/.yamllint-estate.yml").read_bytes()
        )
        (root / ".yamllint.yml").write_bytes(
            (_CANON / "yaml/.yamllint.yml").read_bytes()
        )
        (root / ".github/workflows").mkdir(parents=True)
        (root / ".github/actionlint.yaml").write_bytes(
            (_CANON / "yaml/actionlint.yaml").read_bytes()
        )
        (root / ".github/workflows/ci.yml").write_text(workflow, encoding="utf-8")
        envelope = build_yaml_lint_envelope(root)
        result = runner.run_rule(
            _RULE_ID,
            root,
            envelope_builder=lambda _rule_id, _checkout: typ.cast(
                "runner.RuleEnvelope", envelope
            ),
        )
    return {finding.rule_id for finding in result.findings}


_COMPLIANT_CI: typ.Final = CiChoice(
    installer_before=True,
    installer_present=True,
    invocation=("make lint", True),
    continue_on_error=False,
)
_COMPLIANT_GATE: typ.Final = GateChoice(
    depth=0,
    on_path=True,
    strict="--strict",
    git_fed=True,
    flags=("", False),
    soft=False,
)


@settings(max_examples=40, deadline=None)
@given(gate=_GATES)
def test_yl001_holds_exactly_when_a_binding_strict_git_fed_yamllint_is_reachable(
    gate: GateChoice,
) -> None:
    """The yamllint clause depends on reachability, flags and status alone."""
    found = _evaluate(gate.makefile(), _COMPLIANT_CI.workflow())

    assert ("YL-001" not in found) == gate.provides_yamllint(), (gate, found)
    assert "YL-002" not in found, (gate, found)


@settings(max_examples=40, deadline=None)
@given(ci=_CI)
def test_yl005_holds_exactly_when_an_enabled_lint_step_follows_the_installer(
    ci: CiChoice,
) -> None:
    """The CI clause depends on the command, the step order and error handling."""
    found = _evaluate(_COMPLIANT_GATE.makefile(), ci.workflow())

    assert ("YL-005" not in found) == ci.provides_lint(), (ci, found)
    assert found - {"YL-005"} == set(), (ci, found)
