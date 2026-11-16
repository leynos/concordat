"""Audit nested-cargo CI wiring through real workflow files and the real policy."""

from __future__ import annotations

import dataclasses
import typing as typ

import pygit2
import pytest

from concordat.rules import packages, runner
from concordat.rules.nested_cargo_wiring_envelope import (
    build_nested_cargo_wiring_envelope,
)

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    import pathlib

RULE_ID = "nested-cargo-ci-wiring"
SETUP = "leynos/shared-actions/.github/actions/setup-rust@" + "a" * 40
STEPS = "    steps:\n      - uses: actions/checkout@v7\n"


def workflow(
    *, top: str = "", job: str = "", steps: str = "", setup_with: str = ""
) -> str:
    """Return a one-job workflow with the given env, job env and extra steps."""
    setup = f"      - uses: {SETUP}\n" + (
        f"        with:\n          {setup_with}\n" if setup_with else ""
    )
    return (
        f"name: ci\non: push\n{top}jobs:\n  test:\n    runs-on: ubuntu-latest\n{job}"
        + STEPS
        + setup
        + steps
    )


def put(root: pathlib.Path, text: str, name: str = "ci.yml") -> None:
    """Write a workflow file."""
    path = root / ".github" / "workflows" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def ids(root: pathlib.Path) -> set[str]:
    """Return the rule ids the real policy reports for a checkout."""
    return {finding.rule_id for finding in runner.run_rule(RULE_ID, root).findings}


@dataclasses.dataclass(frozen=True, slots=True)
class Case:
    """One workflow and the findings it must draw."""

    text: str
    expected: set[str]


RUN_ENV = '      - run: echo "CARGO_TARGET_DIR=$RUNNER_TEMP/t" >> "$GITHUB_ENV"\n'
RUN_EXPRESSION_EXPORT = (
    "      - run: |\n"
    "          export CARGO_TARGET_DIR=${{ runner.temp }}/target\n"
    "          cargo test\n"
)
RUN_EXPRESSION_PREFIX = (
    "      - run: CARGO_TARGET_DIR=/cache/${{ github.run_id }} cargo test\n"
)
RUN_COMMENT = (
    "      - run: |\n"
    "          # CARGO_TARGET_DIR=/tmp/target was the old configuration.\n"
    "          cargo test\n"
)
RUN_OTHER_VARIABLE = (
    "      - run: |\n"
    "          export OTHER_CARGO_TARGET_DIR=/tmp/target\n"
    "          cargo test\n"
)
RUN_STABLE_EXPRESSION = (
    "      - run: CARGO_TARGET_DIR=${{ github.workspace }}/target cargo test\n"
)
SCOPED = '      - run: RUSTC_WRAPPER="$SCCACHE_PATH" cargo test\n'
OPT_OUT = "export-rustc-wrapper: false"
CASES = {
    "plain": Case(workflow(), set()),
    "stable-target": Case(
        workflow(top="env:\n  CARGO_TARGET_DIR: ${{ github.workspace }}/target\n"),
        set(),
    ),
    "workflow-env-runner-temp": Case(
        workflow(top="env:\n  CARGO_TARGET_DIR: ${{ runner.temp }}/target\n"),
        {"NC-006"},
    ),
    "job-env-run-id": Case(
        workflow(job="    env:\n      CARGO_TARGET_DIR: /cache/${{ github.run_id }}\n"),
        {"NC-006"},
    ),
    "step-env-mktemp": Case(
        workflow(
            steps="      - run: cargo test\n        env:\n"
            "          CARGO_TARGET_DIR: $(mktemp -d)\n"
        ),
        {"NC-006"},
    ),
    "run-script-assignment": Case(workflow(steps=RUN_ENV), {"NC-006"}),
    "run-script-export-of-an-expression": Case(
        workflow(steps=RUN_EXPRESSION_EXPORT), {"NC-006"}
    ),
    "run-script-prefix-assignment-with-an-expression": Case(
        workflow(steps=RUN_EXPRESSION_PREFIX), {"NC-006"}
    ),
    "run-script-comment-is-not-an-assignment": Case(workflow(steps=RUN_COMMENT), set()),
    "run-script-other-variable-is-not-this-one": Case(
        workflow(steps=RUN_OTHER_VARIABLE), set()
    ),
    "run-script-stable-expression": Case(workflow(steps=RUN_STABLE_EXPRESSION), set()),
    "tmp-path": Case(
        workflow(top="env:\n  CARGO_TARGET_DIR: /tmp/target\n"), {"NC-006"}
    ),
    "opt-out-unnamed": Case(workflow(setup_with=OPT_OUT), {"NC-006"}),
    "opt-out-quoted": Case(
        workflow(setup_with="export-rustc-wrapper: 'false'"), {"NC-006"}
    ),
    "opt-out-scoped-in-run": Case(workflow(setup_with=OPT_OUT, steps=SCOPED), set()),
    "opt-out-job-env": Case(
        workflow(setup_with=OPT_OUT, job="    env:\n      RUSTC_WRAPPER: sccache\n"),
        set(),
    ),
    "export-left-on": Case(workflow(setup_with="export-rustc-wrapper: true"), set()),
}


MSRV_TARGET = "          CARGO_TARGET_DIR: ${{ runner.temp }}/msrv-target\n"
MSRV_CHECK = (
    '      - run: cargo "+${MSRV}" check --all-targets --locked\n        env:\n'
    + MSRV_TARGET
)


def msrv_workflow(*, steps: str, top: str = "", extra_job: str = "") -> str:
    """Return a workflow whose `msrv` job runs *steps* after a checkout."""
    return (
        f"name: ci\non: push\n{top}jobs:\n  msrv:\n    runs-on: ubuntu-latest\n"
        + STEPS
        + '      - run: rustup toolchain install "$MSRV" --profile minimal\n'
        + steps
        + extra_job
    )


TEST_JOB = (
    "  test:\n    runs-on: ubuntu-latest\n" + STEPS + "      - run: cargo nextest run\n"
)
CHECK_ONLY_CASES = {
    "msrv-check-with-a-per-run-target-is-silent": Case(
        msrv_workflow(steps=MSRV_CHECK), set()
    ),
    "clippy-only-job-is-silent": Case(
        msrv_workflow(steps=MSRV_CHECK.replace("check --all-targets", "clippy")),
        set(),
    ),
    "check-and-test-in-one-job-is-flagged": Case(
        msrv_workflow(steps=MSRV_CHECK + "      - run: cargo test\n"), {"NC-006"}
    ),
    "check-then-make-is-flagged": Case(
        msrv_workflow(steps=MSRV_CHECK + "      - run: make test\n"), {"NC-006"}
    ),
    "check-then-a-script-is-flagged": Case(
        msrv_workflow(steps=MSRV_CHECK + "      - run: ./scripts/ci.sh\n"),
        {"NC-006"},
    ),
    "check-beside-an-unknown-action-is-flagged": Case(
        msrv_workflow(steps=MSRV_CHECK + "      - uses: acme/runs-tests@v1\n"),
        {"NC-006"},
    ),
    "global-option-before-the-subcommand-is-flagged": Case(
        msrv_workflow(steps=MSRV_CHECK + "      - run: cargo --verbose test\n"),
        {"NC-006"},
    ),
    "global-option-with-test-after-check-is-flagged": Case(
        msrv_workflow(steps=MSRV_CHECK + "      - run: cargo --locked test\n"),
        {"NC-006"},
    ),
    "absolute-cargo-path-is-flagged": Case(
        msrv_workflow(steps=MSRV_CHECK + "      - run: /usr/bin/cargo test\n"),
        {"NC-006"},
    ),
    "slash-script-without-dot-is-flagged": Case(
        msrv_workflow(steps=MSRV_CHECK + "      - run: scripts/ci\n"), {"NC-006"}
    ),
    "unknown-interpreter-is-flagged": Case(
        msrv_workflow(steps=MSRV_CHECK + "      - run: ruby ci.rb\n"), {"NC-006"}
    ),
    "command-after-if-is-flagged": Case(
        msrv_workflow(steps=MSRV_CHECK + "      - run: if make test; then true; fi\n"),
        {"NC-006"},
    ),
    "command-after-while-is-flagged": Case(
        msrv_workflow(
            steps=MSRV_CHECK + "      - run: while make test; do break; done\n"
        ),
        {"NC-006"},
    ),
    "expression-as-the-command-is-flagged": Case(
        msrv_workflow(steps=MSRV_CHECK + "      - run: ${{ inputs.command }}\n"),
        {"NC-006"},
    ),
    "variable-as-the-command-is-flagged": Case(
        msrv_workflow(steps=MSRV_CHECK + "      - run: $TEST_COMMAND\n"), {"NC-006"}
    ),
    "command-in-a-wildcard-case-arm-is-flagged": Case(
        msrv_workflow(
            steps=MSRV_CHECK
            + "      - run: |\n          case x in\n            *) make test ;;\n"
            "          esac\n"
        ),
        {"NC-006"},
    ),
    "command-after-a-quoted-assignment-is-flagged": Case(
        msrv_workflow(steps=MSRV_CHECK + '      - run: RUST_BACKTRACE="1" make test\n'),
        {"NC-006"},
    ),
    "command-after-an-expanded-assignment-is-flagged": Case(
        msrv_workflow(
            steps=MSRV_CHECK + "      - run: RUST_BACKTRACE=$TRACE make test\n"
        ),
        {"NC-006"},
    ),
    "command-after-a-substituted-assignment-is-flagged": Case(
        msrv_workflow(steps=MSRV_CHECK + '      - run: X="$(true)" make test\n'),
        {"NC-006"},
    ),
    "substitution-inside-an-assignment-prefix-is-flagged": Case(
        msrv_workflow(
            steps=MSRV_CHECK
            + '      - run: RUSTFLAGS="$(make rustflags)" cargo check --locked\n'
        ),
        {"NC-006"},
    ),
    "quoted-variable-assignment-then-a-command-is-flagged": Case(
        msrv_workflow(
            steps=MSRV_CHECK + '      - run: RUST_BACKTRACE="$TRACE" make test\n'
        ),
        {"NC-006"},
    ),
    "quoted-default-assignment-then-a-command-is-flagged": Case(
        msrv_workflow(
            steps=MSRV_CHECK
            + '      - run: RUST_BACKTRACE="${RUST_BACKTRACE:-1}" make test\n'
        ),
        {"NC-006"},
    ),
    "two-assignments-then-a-command-is-flagged": Case(
        msrv_workflow(steps=MSRV_CHECK + '      - run: A="x y" B=$(true) make test\n'),
        {"NC-006"},
    ),
    "substitution-in-a-default-expansion-is-flagged": Case(
        msrv_workflow(
            steps=MSRV_CHECK
            + "      - run: RUSTFLAGS=${RUSTFLAGS:-$(make rustflags)} cargo check\n"
        ),
        {"NC-006"},
    ),
    "a-plain-default-expansion-stays-silent": Case(
        msrv_workflow(
            steps="      - run: RUSTFLAGS=${RUSTFLAGS:-x} cargo check --locked\n"
            "        env:\n" + MSRV_TARGET
        ),
        set(),
    ),
    "a-quoted-assignment-before-cargo-check-stays-silent": Case(
        msrv_workflow(
            steps='      - run: RUSTFLAGS="-D warnings" cargo check --locked\n'
            "        env:\n" + MSRV_TARGET
        ),
        set(),
    ),
    "background-ampersand-then-a-command-is-flagged": Case(
        msrv_workflow(steps=MSRV_CHECK + "      - run: true & make test\n"),
        {"NC-006"},
    ),
    "sed-with-a-trailing-expression-option-is-flagged": Case(
        msrv_workflow(
            steps=MSRV_CHECK
            + "      - run: sed -n 's/a/b/p' -e '1e make test' Cargo.toml\n"
        ),
        {"NC-006"},
    ),
    "sed-execute-command-is-flagged": Case(
        msrv_workflow(
            steps=MSRV_CHECK + "      - run: printf 'x' | sed '1e make test'\n"
        ),
        {"NC-006"},
    ),
    "sed-substitute-execute-flag-is-flagged": Case(
        msrv_workflow(
            steps=MSRV_CHECK + "      - run: sed -n 's/a/make test/e' Cargo.toml\n"
        ),
        {"NC-006"},
    ),
    "sed-second-command-is-flagged": Case(
        msrv_workflow(
            steps=MSRV_CHECK + "      - run: sed -n 's/a/b/;e make test/p' Cargo.toml\n"
        ),
        {"NC-006"},
    ),
    "rustup-run-is-flagged": Case(
        msrv_workflow(steps=MSRV_CHECK + "      - run: rustup run stable make test\n"),
        {"NC-006"},
    ),
    "awk-is-flagged": Case(
        msrv_workflow(
            steps=MSRV_CHECK + "      - run: awk 'BEGIN { system(\"make test\") }'\n"
        ),
        {"NC-006"},
    ),
    "lookalike-checkout-owner-is-flagged": Case(
        msrv_workflow(steps=MSRV_CHECK + "      - uses: acme/checkout@v1\n"),
        {"NC-006"},
    ),
    "local-action-named-like-a-setup-action-is-flagged": Case(
        msrv_workflow(steps=MSRV_CHECK + "      - uses: ./.github/actions/checkout\n"),
        {"NC-006"},
    ),
    "case-and-substitution-script-stays-silent": Case(
        msrv_workflow(
            steps=(
                "      - run: |\n"
                "          msrv=\"$(sed -n 's/^x/y/p' Cargo.toml | head -n 1)\"\n"
                '          test -n "$msrv"\n'
                '          case "$msrv" in *.*.*) ;; *) msrv="$msrv.0" ;; esac\n'
                '          echo "MSRV=${msrv}" >> "$GITHUB_ENV"\n'
            )
            + MSRV_CHECK
        ),
        set(),
    ),
    "a-job-with-no-cargo-command-is-flagged": Case(
        msrv_workflow(steps="      - run: echo hi\n        env:\n" + MSRV_TARGET),
        {"NC-006"},
    ),
    "workflow-env-reaching-a-test-job-is-flagged": Case(
        msrv_workflow(
            steps=MSRV_CHECK.replace(MSRV_TARGET, ""),
            top="env:\n  CARGO_TARGET_DIR: ${{ runner.temp }}/t\n",
            extra_job=TEST_JOB,
        ),
        {"NC-006"},
    ),
    "workflow-env-reaching-only-check-jobs-is-silent": Case(
        msrv_workflow(
            steps=MSRV_CHECK.replace(MSRV_TARGET, ""),
            top="env:\n  CARGO_TARGET_DIR: ${{ runner.temp }}/t\n",
        ),
        set(),
    ),
    "a-test-job-with-a-per-run-target-is-flagged": Case(
        msrv_workflow(
            steps=MSRV_CHECK,
            extra_job=TEST_JOB.replace(
                "      - run: cargo nextest run\n",
                "      - run: cargo nextest run\n        env:\n" + MSRV_TARGET,
            ),
        ),
        {"NC-006"},
    ),
}
CASES |= CHECK_ONLY_CASES


@pytest.mark.parametrize("case", CASES.values(), ids=CASES.keys())
def test_workflows_draw_the_expected_findings(
    tmp_path: pathlib.Path, case: Case
) -> None:
    """The real policy reports each workflow's findings and nothing else."""
    put(tmp_path, case.text)
    assert ids(tmp_path) == case.expected


def test_another_action_with_a_similar_name_is_not_setup_rust(
    tmp_path: pathlib.Path,
) -> None:
    """Only the shared `setup-rust` action exports the wrapper."""
    text = workflow().replace(SETUP, "dtolnay/rust-toolchain@stable") + (
        "      - uses: acme/setup-rust-toolchain@v1\n"
        "        with:\n          export-rustc-wrapper: false\n"
    )
    put(tmp_path, text)
    assert ids(tmp_path) == set()


def test_no_workflows_is_compliant(tmp_path: pathlib.Path) -> None:
    """A repository without workflows has nothing to wire."""
    assert ids(tmp_path) == set()


def test_an_undecodable_workflow_is_indeterminate(tmp_path: pathlib.Path) -> None:
    """A workflow that is not YAML is a fact, never a pass."""
    put(tmp_path, "jobs: [unclosed")
    assert runner.run_rule(RULE_ID, tmp_path).verdict == "indeterminate"


def test_a_clean_workflow_beside_a_bad_one_does_not_hide_it(
    tmp_path: pathlib.Path,
) -> None:
    """Every workflow is audited, not only the first."""
    put(tmp_path, workflow(), "a.yml")
    put(tmp_path, CASES["tmp-path"].text, "b.yml")
    findings = runner.run_rule(RULE_ID, tmp_path).findings
    assert [finding.path for finding in findings] == [".github/workflows/b.yml"]


def test_the_envelope_records_each_place_and_each_job(tmp_path: pathlib.Path) -> None:
    """The facts name where the variable is set and how each job is wired."""
    put(
        tmp_path,
        workflow(
            top="env:\n  CARGO_TARGET_DIR: /tmp/a\n",
            setup_with=OPT_OUT,
            steps=RUN_ENV,
        ),
    )
    (fact,) = build_nested_cargo_wiring_envelope(tmp_path)["workflows"]
    assert [(s["where"], s["value"]) for s in fact["target_settings"]] == [
        ("env", "/tmp/a"),  # noqa: S108 - the value under test
        ("step test/2 run", "$RUNNER_TEMP/t"),
    ]
    assert fact["jobs"] == [
        {"id": "test", "opts_out": True, "wrapper_named": False, "check_only": False}
    ]
    assert [s["job"] for s in fact["target_settings"]] == [None, "test"]


TEMP_TARGET = "  CARGO_TARGET_DIR: ${{ runner.temp }}/target\n"


def result_with(
    monkeypatch: pytest.MonkeyPatch,
    root: pathlib.Path,
    parameters: cabc.Mapping[str, object],
) -> runner.RuleRunResult:
    """Run the rule as if its manifest declared *parameters*.

    Returns
    -------
    runner.RuleRunResult
        The result the package reports under *parameters*.
    """
    defaults = packages.rule_parameters(packages.rule_package_dir(RULE_ID))
    monkeypatch.setattr(
        runner, "_rule_parameters", lambda _rule_dir: defaults | dict(parameters)
    )
    return runner.run_rule(RULE_ID, root)


def temporary_target_checkout(root: pathlib.Path) -> None:
    """Write a repository whose workflow sets a per-run CARGO_TARGET_DIR."""
    pygit2.init_repository(str(root)).remotes.create(
        "origin", "https://github.com/leynos/example.git"
    )
    put(root, workflow(top="env:\n" + TEMP_TARGET))


def exemption(**overrides: str) -> dict[str, str]:
    """Return a complete exemption for the workflow, with overrides."""
    return {
        "repository": "leynos/example",
        "path": ".github/workflows/ci.yml",
        "rationale": "benchmark lane needs a private tree",
        "review_trigger": "review when the benchmark lane moves",
    } | overrides


def test_a_complete_exemption_takes_the_workflow_out(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Repository, workflow, rationale and review trigger suppress NC-006."""
    temporary_target_checkout(tmp_path)
    assert {f.rule_id for f in runner.run_rule(RULE_ID, tmp_path).findings} == {
        "NC-006"
    }
    result = result_with(monkeypatch, tmp_path, {"exemptions": [exemption()]})
    assert not result.findings


@pytest.mark.parametrize(
    "blank", [{"rationale": " "}, {"review_trigger": ""}], ids=["rationale", "trigger"]
)
def test_an_exemption_without_a_reviewed_record_suppresses_nothing(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, blank: dict[str, str]
) -> None:
    """A blank rationale or review trigger is not a record."""
    temporary_target_checkout(tmp_path)
    result = result_with(monkeypatch, tmp_path, {"exemptions": [exemption(**blank)]})
    assert {f.rule_id for f in result.findings} == {"NC-006"}


def test_an_exemption_does_not_travel_to_another_repository(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same workflow under another repository's name is audited."""
    temporary_target_checkout(tmp_path)
    result = result_with(
        monkeypatch, tmp_path, {"exemptions": [exemption(repository="leynos/other")]}
    )
    assert {f.rule_id for f in result.findings} == {"NC-006"}
