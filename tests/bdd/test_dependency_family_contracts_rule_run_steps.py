"""Behavioural tests for `concordat artefact rule run` on DF-001 to DF-006.

These scenarios run the real policy over a real manifest, lock and Dependabot
configuration through `cli.main`, so they cover the whole boundary an operator
meets: a checkout on disk, the envelope built from it, Conftest's verdict, the
process exit status and the rendered table. The Rego suite proves the clauses;
this proves the command delivers them.
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

scenarios("features/dependency_family_contracts_rule_run.feature")

RULE_ID: typ.Final = "dependency-family-contracts"

_CARGO: typ.Final = """\
[package]
name = "demo"
version = "0.1.0"
edition = "2024"

[dependencies]
pyo3 = "0.28.3"
pyo3-build-config = "0.28.3"
"""
_LOCK: typ.Final = """\
version = 4

[[package]]
name = "pyo3"
version = "0.28.3"

[[package]]
name = "pyo3-build-config"
version = "0.28.3"
"""
_CONFIG: typ.Final = """\
version: 2
updates:
  - package-ecosystem: cargo
    directory: /
    schedule:
      interval: daily
    groups:
{narrow}      minor-and-patch:
        patterns: ['*']
        update-types: [minor, patch]
"""
_NARROW: typ.Final = "      pyo3:\n        patterns: ['pyo3', 'pyo3-build-config']\n"
_EXCEPTION: typ.Final = """\
dependencies:
  family_exceptions:
    - relationship: pyo3-build-config-family
      rationale: the extension stays on the 0.28 generation
      review_trigger: review when PyO3 0.30 ships
"""


_PYPROJECT: typ.Final = """\
[project]
name = "demo"
version = "0.1.0"

[dependency-groups]
dev = ["pylint==4.0.9", "astroid==4.0.4"]
"""
_UV_LOCK: typ.Final = """\
version = 1

[[package]]
name = "pylint"
version = "4.0.9"

[[package]]
name = "astroid"
version = "4.0.4"
"""
_UV_CONFIG: typ.Final = """\
version: 2
updates:
  - package-ecosystem: uv
    directory: /
    schedule:
      interval: daily
    groups:
{narrow}      minor-and-patch:
        patterns: ['*']
        update-types: [minor, patch]
"""
_PYLINT_GROUP: typ.Final = (
    "      pylint-astroid:\n        patterns: ['pylint', 'astroid']\n"
)


_MATURIN_PROJECT: typ.Final = """\
[project]
name = "demo"
version = "0.1.0"
dependencies = ["maturin==1.13.3"]
"""
_MATURIN_CONFIG: typ.Final = """\
version: 2
{definition}updates:
  - package-ecosystem: cargo
    directory: /
    groups:
      pyo3:
        patterns: ['pyo3', 'pyo3-build-config']
      minor-and-patch:
        patterns: ['*']
        update-types: [minor, patch]
{cargo_join}    schedule:
      interval: daily
  - package-ecosystem: uv
    directory: /
    groups:
      minor-and-patch:
        patterns: ['*']
        update-types: [minor, patch]
{uv_join}    schedule:
      interval: daily
"""
_MULTI_DEFINITION: typ.Final = (
    "multi-ecosystem-groups:\n  maturin-pyo3:\n    schedule:\n      interval: daily\n"
)


@pytest.fixture
def family_checkout(tmp_path: pathlib.Path) -> pathlib.Path:
    """Provide the checkout directory one scenario audits."""
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    return checkout


def _write_pyo3(checkout: pathlib.Path, *, narrow: str, concordat: str = "") -> None:
    """Write a PyO3 crate, its lock and a Dependabot configuration."""
    (checkout / "Cargo.toml").write_text(_CARGO, encoding="utf-8")
    (checkout / "Cargo.lock").write_text(_LOCK, encoding="utf-8")
    github = checkout / ".github"
    github.mkdir()
    (github / "dependabot.yml").write_text(
        _CONFIG.format(narrow=narrow), encoding="utf-8"
    )
    if concordat:
        (checkout / ".concordat").write_text(concordat, encoding="utf-8")


def _write_pylint_pair(checkout: pathlib.Path, *, narrow: str) -> None:
    """Write exact Pylint and Astroid pins, their uv lock and a uv entry."""
    (checkout / "pyproject.toml").write_text(_PYPROJECT, encoding="utf-8")
    (checkout / "uv.lock").write_text(_UV_LOCK, encoding="utf-8")
    github = checkout / ".github"
    github.mkdir()
    (github / "dependabot.yml").write_text(
        _UV_CONFIG.format(narrow=narrow), encoding="utf-8"
    )


def _write_maturin_pyo3(checkout: pathlib.Path, *, joined: bool) -> None:
    """Write an exactly pinned maturin beside a PyO3 crate, joined or not."""
    (checkout / "Cargo.toml").write_text(_CARGO, encoding="utf-8")
    (checkout / "Cargo.lock").write_text(_LOCK, encoding="utf-8")
    (checkout / "pyproject.toml").write_text(_MATURIN_PROJECT, encoding="utf-8")
    (checkout / "uv.lock").write_text(
        'version = 1\n\n[[package]]\nname = "maturin"\nversion = "1.13.3"\n',
        encoding="utf-8",
    )
    github = checkout / ".github"
    github.mkdir()
    join = "    multi-ecosystem-group: maturin-pyo3\n    patterns: ['{}']\n"
    (github / "dependabot.yml").write_text(
        _MATURIN_CONFIG.format(
            definition=_MULTI_DEFINITION if joined else "",
            cargo_join=join.format("pyo3") if joined else "",
            uv_join=join.format("maturin") if joined else "",
        ),
        encoding="utf-8",
    )


@given(
    "a checkout pinning maturin exactly with both ecosystems in one "
    "multi-ecosystem group"
)
def given_maturin_multi_group(family_checkout: pathlib.Path) -> None:
    """Write the protected shape: both entries join one defined group."""
    _write_maturin_pyo3(family_checkout, joined=True)


@given("a checkout pinning maturin exactly with no multi-ecosystem group")
def given_maturin_unjoined(family_checkout: pathlib.Path) -> None:
    """Write the unprotected shape: separate entries, an exact pin and no bound."""
    _write_maturin_pyo3(family_checkout, joined=False)


def _write_workspace_member(checkout: pathlib.Path, *, narrow: str) -> None:
    """Write a root workspace whose member crate declares the PyO3 pair."""
    (checkout / "Cargo.toml").write_text(
        '[workspace]\nmembers = ["native"]\n\n[workspace.dependencies]\n'
        'pyo3 = "0.28.3"\npyo3-build-config = "0.28.3"\n',
        encoding="utf-8",
    )
    member = checkout / "native"
    member.mkdir()
    (member / "Cargo.toml").write_text(
        '[package]\nname = "native"\nversion = "0.1.0"\n\n[dependencies]\n'
        "pyo3 = { workspace = true }\npyo3-build-config = { workspace = true }\n",
        encoding="utf-8",
    )
    (checkout / "Cargo.lock").write_text(_LOCK, encoding="utf-8")
    github = checkout / ".github"
    github.mkdir()
    (github / "dependabot.yml").write_text(
        _CONFIG.format(narrow=narrow), encoding="utf-8"
    )


@given("a checkout whose workspace member declares PyO3 under the catch-all group")
def given_member_catch_all(family_checkout: pathlib.Path) -> None:
    """Write the split shape inside a workspace member."""
    _write_workspace_member(family_checkout, narrow="")


@given("a checkout whose workspace member declares PyO3 in a narrow Dependabot group")
def given_member_grouped(family_checkout: pathlib.Path) -> None:
    """Write the compliant shape inside a workspace member."""
    _write_workspace_member(family_checkout, narrow=_NARROW)


@given("a checkout pinning Pylint and Astroid exactly with a shared Dependabot group")
def given_pylint_grouped(family_checkout: pathlib.Path) -> None:
    """Write the compliant shape: the pair in one group ahead of the catch-all."""
    _write_pylint_pair(family_checkout, narrow=_PYLINT_GROUP)


@given("a checkout pinning Pylint and Astroid exactly under the catch-all group")
def given_pylint_catch_all(family_checkout: pathlib.Path) -> None:
    """Write the split shape: both pins fall to the catch-all."""
    _write_pylint_pair(family_checkout, narrow="")


@given("a checkout that declares no related packages")
def given_nothing_related(family_checkout: pathlib.Path) -> None:
    """Leave the checkout empty: no relationship applies."""


@given("a checkout whose PyO3 packages share a narrow Dependabot group")
def given_grouped(family_checkout: pathlib.Path) -> None:
    """Write the compliant shape: one narrow group ahead of the catch-all."""
    _write_pyo3(family_checkout, narrow=_NARROW)


@given("a checkout whose PyO3 packages fall to the catch-all group")
def given_catch_all(family_checkout: pathlib.Path) -> None:
    """Write the split shape: no narrow group."""
    _write_pyo3(family_checkout, narrow="")


@given(
    "a checkout whose PyO3 packages fall to the catch-all group "
    "with a reviewed exception"
)
def given_exception(family_checkout: pathlib.Path) -> None:
    """Write the split shape with an exception that names its review trigger."""
    _write_pyo3(family_checkout, narrow="", concordat=_EXCEPTION)


@given("a checkout whose PyO3 crate sits in an undeclared subdirectory")
def given_undeclared_crate(family_checkout: pathlib.Path) -> None:
    """Write the crate under `rust/` with no `.concordat` surface declaration."""
    crate = family_checkout / "rust"
    crate.mkdir()
    (crate / "Cargo.toml").write_text(_CARGO, encoding="utf-8")
    (crate / "Cargo.lock").write_text(_LOCK, encoding="utf-8")
    github = family_checkout / ".github"
    github.mkdir()
    (github / "dependabot.yml").write_text(
        _CONFIG.format(narrow=_NARROW), encoding="utf-8"
    )


@given("a checkout that declares PyO3 but has no Dependabot configuration")
def given_no_dependabot(family_checkout: pathlib.Path) -> None:
    """Write the crate and lock without `.github/dependabot.yml`."""
    (family_checkout / "Cargo.toml").write_text(_CARGO, encoding="utf-8")
    (family_checkout / "Cargo.lock").write_text(_LOCK, encoding="utf-8")


@when("I audit the checkout for its dependency families")
def when_audit_checkout(
    family_checkout: pathlib.Path,
    cli_invocation: dict[str, RunResult],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Invoke the command and record its output and exit status."""
    try:
        returncode = cli.main(
            ["artefact", "rule", "run", RULE_ID, "--repo", str(family_checkout)],
        )
    except SystemExit as exc:
        returncode = int(exc.code or 0)
    captured = capsys.readouterr()
    cli_invocation["result"] = RunResult(
        stdout=captured.out,
        stderr=captured.err,
        returncode=returncode,
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
    assert "DF-0" not in stdout, stdout


@then(parsers.cfparse('the audit output reports "{message}"'))
def then_audit_reports(cli_invocation: dict[str, RunResult], message: str) -> None:
    """Assert the rendered output carries the named rule or message."""
    stdout = cli_invocation["result"].stdout
    assert message in stdout, stdout
