"""Behavioural tests for `concordat artefact rule run` on the rust-lint-tables rule.

These scenarios run the real policy over real manifests through `cli.main`, so
they cover the whole boundary an operator meets: a checkout on disk, the
envelope built from it, the canon baseline the runner loads beside the
parameters, Conftest's verdict, the process exit status and the rendered table.
The Rego suite proves the clauses; this proves the command delivers them for a
workspace designed as one, one grown from a single crate, and a Python
extension.
"""

from __future__ import annotations

import pathlib
import re
import typing as typ

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from concordat import cli

from .conftest import RunResult

scenarios("features/rust_lint_tables_rule_run.feature")

RULE_ID: typ.Final = "rust-lint-tables"
BASELINE: typ.Final = (
    pathlib.Path(__file__).resolve().parents[2]
    / "platform-standards"
    / "canon"
    / "lint-rules"
    / "rust-lint-tables"
    / "data"
    / "baseline.toml"
).read_text(encoding="utf-8")
WORKSPACE_LINTS: typ.Final = BASELINE
PACKAGE_LINTS: typ.Final = BASELINE.replace("[workspace.lints.", "[lints.")
VIRTUAL: typ.Final = '[workspace]\nmembers = ["crates/*"]\nresolver = "3"\n\n'
THIN_TABLES: typ.Final = (
    '[lints.clippy]\ndisallowed_methods = "deny"\n'
    'missing_docs_in_private_items = "deny"\n\n'
    '[lints.rust]\nmissing_docs = "deny"\n'
    'unexpected_cfgs = { level = "warn", check-cfg = ["cfg(loom)"] }\n'
)


def _package(name: str, extra: str = "") -> str:
    """Return a `[package]` table followed by *extra*."""
    return f'[package]\nname = "{name}"\nversion = "0.1.0"\n{extra}'


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


def _workspace_with_members(checkout: pathlib.Path, member_b: str) -> None:
    """Write a virtual workspace whose member `a` inherits and `b` is *member_b*."""
    _write(
        checkout,
        {
            "Cargo.toml": VIRTUAL + WORKSPACE_LINTS,
            "crates/a/Cargo.toml": _package("a", "\n[lints]\nworkspace = true\n"),
            "crates/b/Cargo.toml": member_b,
        },
    )


@given("a checkout with no Cargo manifest")
def given_no_manifest(rust_checkout: pathlib.Path) -> None:
    """Leave a checkout that holds only prose."""
    _write(rust_checkout, {"README.md": "# Demo\n"})


@given("a virtual workspace whose members all inherit the baseline")
def given_designed_clean(rust_checkout: pathlib.Path) -> None:
    """Write a designed workspace in which both members opt in."""
    _workspace_with_members(
        rust_checkout, _package("b", "\n[lints]\nworkspace = true\n")
    )


@given("a virtual workspace with a member that sets no lint tables")
def given_member_bare(rust_checkout: pathlib.Path) -> None:
    """Write a designed workspace in which member `b` was added without tables."""
    _workspace_with_members(rust_checkout, _package("b"))


@given("a virtual workspace with a member whose manifest is not TOML")
def given_member_unreadable(rust_checkout: pathlib.Path) -> None:
    """Write a designed workspace whose member manifest cannot be decoded."""
    _workspace_with_members(rust_checkout, "[package\nname = \n")


@given("a root package whose workspace defines lint tables it does not inherit")
def given_root_not_opted_in(rust_checkout: pathlib.Path) -> None:
    """Write a grown workspace: the root package never set `workspace = true`."""
    _write(
        rust_checkout,
        {
            "Cargo.toml": _package("app") + "\n" + VIRTUAL + WORKSPACE_LINTS,
            "crates/b/Cargo.toml": _package("b", "\n[lints]\nworkspace = true\n"),
        },
    )


def _extension(checkout: pathlib.Path, lints: str) -> None:
    """Write a maturin project whose `rust_extension` crate carries *lints*."""
    _write(
        checkout,
        {
            "pyproject.toml": (
                '[build-system]\nrequires = ["maturin>=1"]\n'
                'build-backend = "maturin"\n\n[project]\nname = "demo"\n'
            ),
            "rust_extension/Cargo.toml": _package(
                "ext", '\n[dependencies]\npyo3 = "0.30"\n\n' + lints
            ),
        },
    )


@given("a Python project whose rust_extension crate sets four lints")
def given_thin_extension(rust_checkout: pathlib.Path) -> None:
    """Write femtologging's shape: two Clippy lints and two rustc lints."""
    _extension(rust_checkout, THIN_TABLES)


@given("a Python project whose rust_extension crate carries the full baseline")
def given_full_extension(rust_checkout: pathlib.Path) -> None:
    """Write the same extension with the whole baseline as package-level tables."""
    _extension(rust_checkout, PACKAGE_LINTS)


@when("I audit the checkout for Rust lint tables")
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
    """Assert the process exit status."""
    assert cli_invocation["result"].returncode == code, cli_invocation["result"].stdout


@then("the audit table reports zero findings")
def then_audit_clean(cli_invocation: dict[str, RunResult]) -> None:
    """Assert the table reports a compliant checkout."""
    stdout = cli_invocation["result"].stdout
    assert re.search(r"\bcompliant\b", stdout), stdout
    assert "LT-0" not in stdout, stdout


@then(parsers.cfparse('the audit output reports "{rule_id}" "{message}"'))
def then_audit_reports(
    cli_invocation: dict[str, RunResult], rule_id: str, message: str
) -> None:
    """Assert the rendered table carries a finding with this rule and text."""
    stdout = re.sub(r"\s+", " ", cli_invocation["result"].stdout)
    assert rule_id in stdout, stdout
    assert message in stdout, stdout
