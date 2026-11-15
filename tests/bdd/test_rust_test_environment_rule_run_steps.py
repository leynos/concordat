"""Behavioural tests for `concordat artefact rule run` on TE-001 to TE-004.

These scenarios run the real policy over real Cargo manifests, Clippy
configuration and Rust sources through `cli.main`, so they cover the whole
boundary an operator meets: a checkout on disk, the envelope built from it,
Conftest's verdict, the process exit status and the rendered table. The Rego
suite proves the clauses; this proves the command delivers them.
"""

from __future__ import annotations

import re
import typing as typ

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from concordat import cli

from .conftest import RunResult

if typ.TYPE_CHECKING:
    import pathlib

scenarios("features/rust_test_environment_rule_run.feature")

RULE_ID: typ.Final = "rust-test-environment"
_PACKAGE: typ.Final = '[package]\nname = "demo"\nversion = "0.1.0"\n'
_GUARD: typ.Final = (
    'disallowed-methods = ["std::env::set_var", "std::env::remove_var",'
    ' "std::env::set_current_dir"]\n'
)
_EXPECT: typ.Final = (
    '#[expect(clippy::disallowed_methods, reason = "adapter behind the seam")]\n'
)


@pytest.fixture
def rust_checkout(tmp_path: pathlib.Path) -> pathlib.Path:
    """Provide the checkout directory one scenario audits."""
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    return checkout


def _write(checkout: pathlib.Path, files: dict[str, str]) -> None:
    """Write *files* under the checkout, creating directories."""
    for relative, text in files.items():
        path = checkout / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def _guarded(overrides: dict[str, str] | None = None) -> dict[str, str]:
    """Return a compliant crate, with *overrides* replacing files by path."""
    base = {
        "Cargo.toml": _PACKAGE,
        "clippy.toml": _GUARD,
        "src/lib.rs": f"{_EXPECT}fn adapter() {{}}\n",
        "tests/it.rs": "#[test]\nfn it() {}\n",
    }
    return {**base, **(overrides or {})}


@given("a checkout with no Cargo manifest")
def given_no_manifest(rust_checkout: pathlib.Path) -> None:
    """Leave a checkout that holds only prose."""
    _write(rust_checkout, {"README.md": "# Demo\n"})


@given("a crate whose Clippy configuration disallows the environment calls")
def given_compliant(rust_checkout: pathlib.Path) -> None:
    """Write the reference crate."""
    _write(rust_checkout, _guarded())


@given("a crate with no Clippy configuration")
def given_no_clippy(rust_checkout: pathlib.Path) -> None:
    """Write a crate that has no `clippy.toml` at all."""
    files = _guarded()
    del files["clippy.toml"]
    _write(rust_checkout, files)


@given("a crate that depends on temp-env without disallowing its functions")
def given_wrapper(rust_checkout: pathlib.Path) -> None:
    """Write a crate whose dev-dependency changes the environment."""
    _write(
        rust_checkout,
        _guarded({"Cargo.toml": _PACKAGE + '[dev-dependencies]\ntemp-env = "0.3"\n'}),
    )


@given("a crate that allows the lint in a test file")
def given_allow(rust_checkout: pathlib.Path) -> None:
    """Write a crate whose test file allows the lint at crate level."""
    _write(
        rust_checkout,
        _guarded({"tests/it.rs": "#![allow(clippy::disallowed_methods)]\n"}),
    )


@given("a crate whose Cargo manifest allows the lint")
def given_lints_table(rust_checkout: pathlib.Path) -> None:
    """Write a crate that lowers the lint in `[lints.clippy]`."""
    _write(
        rust_checkout,
        _guarded({
            "Cargo.toml": _PACKAGE + '[lints.clippy]\ndisallowed_methods = "allow"\n'
        }),
    )


@given("a crate whose Clippy configuration is not valid TOML")
def given_invalid_clippy(rust_checkout: pathlib.Path) -> None:
    """Write a `clippy.toml` the audit cannot decode."""
    _write(rust_checkout, _guarded({"clippy.toml": "disallowed-methods = [\n"}))


@when("I audit the checkout for its test environment")
def when_audit_checkout(
    rust_checkout: pathlib.Path,
    cli_invocation: dict[str, RunResult],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Invoke the command on the scenario's checkout and record the result."""
    try:
        returncode = cli.main(
            ["artefact", "rule", "run", RULE_ID, "--repo", str(rust_checkout)],
        )
    except SystemExit as exc:
        returncode = int(exc.code or 0)
    captured = capsys.readouterr()
    cli_invocation["result"] = RunResult(
        stdout=captured.out, stderr=captured.err, returncode=returncode
    )


@then(parsers.cfparse("the audit exit status is {code:d}"))
def then_audit_exit_status(cli_invocation: dict[str, RunResult], code: int) -> None:
    """Assert the recorded exit status."""
    result = cli_invocation["result"]
    assert result.returncode == code, result.stderr or result.stdout


@then("the audit table reports zero findings")
def then_audit_clean(cli_invocation: dict[str, RunResult]) -> None:
    """Assert the table names the compliant verdict and no findings."""
    stdout = cli_invocation["result"].stdout
    assert re.search(r"\bcompliant\b", stdout), stdout
    assert "TE-0" not in stdout, stdout


@then(parsers.cfparse('the audit output reports "{rule_id}" "{message}"'))
def then_audit_reports(
    cli_invocation: dict[str, RunResult], rule_id: str, message: str
) -> None:
    """Assert the rendered table carries one finding with this rule and text."""
    stdout = cli_invocation["result"].stdout
    assert any(rule_id in line and message in line for line in stdout.splitlines()), (
        stdout
    )
