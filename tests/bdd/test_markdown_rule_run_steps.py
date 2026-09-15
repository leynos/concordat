"""Behavioural tests for `concordat artefact rule run markdown-formatting-baseline`."""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
import typing as typ

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from concordat import cli

from .conftest import RunResult

if typ.TYPE_CHECKING:
    import types

    from tests.conftest import CmdMox

scenarios("features/markdown_rule_run.feature")

RULE_ID = "markdown-formatting-baseline"
NAMESPACE = "canon.lint_rules.markdown_formatting_baseline"
PACKAGE_DIR = (
    pathlib.Path(__file__).resolve().parents[2]
    / "platform-standards"
    / "canon"
    / "lint-rules"
    / RULE_ID
)
ENVELOPES_DIR = PACKAGE_DIR / "fixtures" / "envelopes"

# The repository-relative path and one-based line a finding cites.
FailureLocation = tuple[str, int]


def _load_generator() -> types.ModuleType:
    """Import the package's `fixtures/generate.py` by path."""
    spec = importlib.util.spec_from_file_location(
        "markdown_formatting_baseline_generate_bdd",
        PACKAGE_DIR / "fixtures" / "generate.py",
    )
    if spec is None or spec.loader is None:
        message = "could not load the fixture generator"
        raise ImportError(message)
    module = importlib.util.module_from_spec(spec)
    # `Scenario` is a dataclass under `from __future__ import annotations`;
    # dataclasses resolve those string annotations through `sys.modules`, so
    # the module must be registered before its body runs.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _conftest_result(failures: list[dict[str, object]]) -> str:
    """Render a conftest JSON document in the observed output shape."""
    result: dict[str, object] = {
        "filename": "envelope.json",
        "namespace": NAMESPACE,
        "successes": 30 - len(failures),
    }
    if failures:
        result["failures"] = failures
    return json.dumps([result])


def _failure(
    rule_id: str,
    verdict: str,
    location: FailureLocation,
    msg: str,
) -> dict[str, object]:
    """Build one conftest failure entry in the observed metadata shape."""
    path, line = location
    return {
        "msg": msg,
        "metadata": {
            "line": line,
            "path": path,
            "query": f"data.{NAMESPACE}.deny",
            "rule_id": rule_id,
            "severity": "error",
            "verdict": verdict,
        },
    }


@pytest.fixture
def checkout(tmp_path: pathlib.Path) -> pathlib.Path:
    """Provide an empty checkout directory for the scenario."""
    return tmp_path / "checkout"


@given(
    parsers.cfparse(
        'a Markdown checkout laid out from the "{scenario}" fixture scenario'
    )
)
def given_markdown_checkout(checkout: pathlib.Path, scenario: str) -> None:
    """Lay the named generator scenario out as the checkout under audit."""
    generate = _load_generator()
    checkout.mkdir()
    generate.lay_out(generate.SCENARIOS[scenario], checkout)


@given(parsers.cfparse('makeutil reports the Markdown "{fixture}" fixture facts'))
def given_makeutil_fixture(cmd_mox: CmdMox, fixture: str) -> None:
    """Program the fake makeutil with the checked-in fixture report."""
    envelope = json.loads((ENVELOPES_DIR / f"{fixture}.json").read_text())
    report = envelope["makefile"]
    exit_code = 0 if report["parse"]["status"] == "complete" else 1
    cmd_mox.mock("makeutil").returns(exit_code=exit_code, stdout=json.dumps(report))


@given("conftest reports no Markdown failures")
def given_conftest_clean(cmd_mox: CmdMox) -> None:
    """Program the fake conftest with a passing result."""
    cmd_mox.mock("conftest").returns(exit_code=0, stdout=_conftest_result([]))


@given("conftest reports the mdformat wrapper failures")
def given_conftest_wrapper(cmd_mox: CmdMox) -> None:
    """Program the fake conftest with the PD-002 and PD-003 wrapper failures."""
    failures = [
        _failure(
            "PD-002",
            "noncompliant",
            ("Makefile", 0),
            'no recipe reachable from "check-fmt" runs mdtablefix',
        ),
        _failure(
            "PD-003",
            "noncompliant",
            ("Makefile", 8),
            '"fmt"-path recipe delegates to the mdformat-all wrapper; '
            "call mdtablefix directly",
        ),
    ]
    cmd_mox.mock("conftest").returns(exit_code=1, stdout=_conftest_result(failures))


@given("conftest reports the malformed configuration failure")
def given_conftest_malformed_config(cmd_mox: CmdMox) -> None:
    """Program the fake conftest with the PD-005 indeterminate failure."""
    failure = _failure(
        "PD-005",
        "indeterminate",
        (".markdownlint-cli2.jsonc", 0),
        ".markdownlint-cli2.jsonc could not be decoded: invalid JSON at line 4",
    )
    cmd_mox.mock("conftest").returns(exit_code=1, stdout=_conftest_result([failure]))


@given("conftest reports the shell lint failure")
def given_conftest_shell_lint(cmd_mox: CmdMox) -> None:
    """Program the fake conftest with the PD-006 shell-step failure."""
    failure = _failure(
        "PD-006",
        "noncompliant",
        (".github/workflows/ci.yml", 0),
        'job "lint-test" lints Markdown from a shell step (Lint Markdown); '
        "use DavidAnson/markdownlint-cli2-action",
    )
    cmd_mox.mock("conftest").returns(exit_code=1, stdout=_conftest_result([failure]))


@when("I run the Markdown rule against the checkout")
def when_run_rule(
    checkout: pathlib.Path,
    cmd_mox: CmdMox,
    cli_invocation: dict[str, RunResult],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Invoke the CLI and capture its output and exit status."""
    cmd_mox.replay()
    try:
        returncode = cli.main(
            ["artefact", "rule", "run", RULE_ID, "--repo", str(checkout)],
        )
    except SystemExit as exc:
        returncode = int(exc.code or 0)
    cmd_mox.verify()
    captured = capsys.readouterr()
    cli_invocation["result"] = RunResult(
        stdout=captured.out,
        stderr=captured.err,
        returncode=returncode,
    )


@then(parsers.cfparse("the Markdown rule exit status is {code:d}"))
def then_exit_status(cli_invocation: dict[str, RunResult], code: int) -> None:
    """Assert the recorded exit status."""
    result = cli_invocation["result"]
    assert result.returncode == code, result.stderr or result.stdout


@then("the table output reports the Markdown rule as compliant")
def then_compliant(cli_invocation: dict[str, RunResult]) -> None:
    """Assert the table names the compliant verdict and no findings."""
    stdout = cli_invocation["result"].stdout
    assert f"{RULE_ID}: compliant" in stdout, stdout
    assert "PD-" not in stdout, stdout


@then("the output contains a PD-003 finding citing Makefile line 8")
def then_pd003_with_line(cli_invocation: dict[str, RunResult]) -> None:
    """Assert the PD-003 finding carries its source location."""
    stdout = cli_invocation["result"].stdout
    assert "PD-003" in stdout, stdout
    assert "Makefile:8" in stdout, stdout
    assert "mdformat-all" in stdout, stdout


@then(parsers.cfparse('the output contains a PD-002 finding naming "{target}"'))
def then_pd002_names_target(
    cli_invocation: dict[str, RunResult],
    target: str,
) -> None:
    """Assert the PD-002 finding names the audited target."""
    stdout = cli_invocation["result"].stdout
    assert "PD-002" in stdout, stdout
    assert f'"{target}"' in stdout, stdout


@then("the output reports PD-005 as indeterminate")
def then_pd005_indeterminate(cli_invocation: dict[str, RunResult]) -> None:
    """Assert the indeterminate verdict is surfaced with the configuration path."""
    stdout = cli_invocation["result"].stdout
    assert "PD-005" in stdout, stdout
    assert "indeterminate" in stdout, stdout
    assert ".markdownlint-cli2.jsonc" in stdout, stdout


@then("the output contains a PD-006 finding naming the CI workflow")
def then_pd006_names_workflow(cli_invocation: dict[str, RunResult]) -> None:
    """Assert the PD-006 finding cites the workflow file."""
    stdout = cli_invocation["result"].stdout
    assert "PD-006" in stdout, stdout
    assert ".github/workflows/ci.yml" in stdout, stdout
