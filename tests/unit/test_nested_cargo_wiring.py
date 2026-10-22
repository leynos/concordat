"""Audit nested-cargo CI wiring through real workflow files and the real policy."""

from __future__ import annotations

import dataclasses
import typing as typ

import pytest

from concordat.rules import runner
from concordat.rules.nested_cargo_wiring_envelope import (
    build_nested_cargo_wiring_envelope,
)

if typ.TYPE_CHECKING:
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
    assert fact["jobs"] == [{"id": "test", "opts_out": True, "wrapper_named": False}]
