"""Behavioural tests for `concordat artefact rule run` on whitaker-exclusions.

These scenarios run the real policy over real manifests and `dylint.toml` files
through `cli.main`, so they cover the whole boundary an operator meets: a
checkout on disk, the envelope built from it, Conftest's verdict, the process
exit status and the rendered table, for a workspace designed as one, one grown
from a single crate, and a Python project's extension.
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

scenarios("features/whitaker_exclusions_rule_run.feature")

RULE_ID: typ.Final = "whitaker-exclusions"
WORKSPACE: typ.Final = '[workspace]\nmembers = ["crates/*"]\nresolver = "3"\n'
WITH_REASON: typ.Final = (
    "[no_std_fs_operations]\n# The loader opens caller-supplied paths.\n"
    'excluded_paths = ["loader"]\n'
)
FEMTOLOGGING_DYLINT: typ.Final = (
    "# Ambient std::fs access: the library mirrors CPython's logging, and the\n"
    "# test crates stage tempdir fixtures.\n"
    "[no_std_fs_operations]\n"
    'excluded_crates = ["_femtologging_rs", "file_handler_tests", "heavy"]\n'
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


def _workspace(checkout: pathlib.Path, dylint: str) -> None:
    """Write a virtual workspace with one member and this `dylint.toml`."""
    _write(
        checkout,
        {
            "Cargo.toml": WORKSPACE,
            "crates/a/Cargo.toml": _package("a"),
            "dylint.toml": dylint,
        },
    )


@given("a checkout with no dylint configuration")
def given_no_config(rust_checkout: pathlib.Path) -> None:
    """Leave a checkout that holds a manifest and no Whitaker configuration."""
    _write(rust_checkout, {"Cargo.toml": _package("app")})


@given("a workspace whose dylint.toml excludes a path with a reason")
def given_path_with_reason(rust_checkout: pathlib.Path) -> None:
    """Write a path exclusion with a comment above its key."""
    _workspace(rust_checkout, WITH_REASON)


@given("a workspace whose dylint.toml excludes a path without a reason")
def given_path_without_reason(rust_checkout: pathlib.Path) -> None:
    """Write the same exclusion with no comment."""
    _workspace(rust_checkout, '[no_std_fs_operations]\nexcluded_paths = ["loader"]\n')


@given("a root package whose dylint.toml excludes the whole root crate")
def given_root_crate_excluded(rust_checkout: pathlib.Path) -> None:
    """Write a grown workspace that excludes its own root package."""
    _write(
        rust_checkout,
        {
            "Cargo.toml": _package("app", "\n") + WORKSPACE,
            "src/lib.rs": "",
            "crates/b/Cargo.toml": _package("b"),
            "dylint.toml": (
                "[no_std_fs_operations]\n# The root crate is ambient.\n"
                'excluded_crates = ["app"]\n'
            ),
        },
    )


@given("a Python project whose rust_extension excludes three whole crates")
def given_extension(rust_checkout: pathlib.Path) -> None:
    """Write femtologging's shape: a standalone crate and its test crates."""
    _write(
        rust_checkout,
        {
            "pyproject.toml": '[project]\nname = "demo"\n',
            "rust_extension/Cargo.toml": _package(
                "femtologging_rs",
                "\n[dev-dependencies]\n"
                '_femtologging_rs = { path = ".", package = "femtologging_rs" }\n',
            ),
            "rust_extension/tests/file_handler_tests.rs": "",
            "rust_extension/tests/heavy/main.rs": "",
            "rust_extension/dylint.toml": FEMTOLOGGING_DYLINT,
        },
    )


@given("a workspace whose dylint.toml excludes a crate that does not exist")
def given_stale(rust_checkout: pathlib.Path) -> None:
    """Write an exclusion of a crate the workspace never had."""
    _workspace(
        rust_checkout,
        '[no_std_fs_operations]\n# Removed long ago.\nexcluded_crates = ["ghost"]\n',
    )


@given("a workspace whose dylint.toml is not TOML")
def given_unreadable(rust_checkout: pathlib.Path) -> None:
    """Write a configuration that cannot be decoded."""
    _workspace(rust_checkout, "[whitaker\nexperimental = \n")


@when("I audit the checkout for Whitaker exclusions")
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
    assert "WX-0" not in stdout, stdout


@then(parsers.cfparse('the audit output reports "{rule_id}" "{message}"'))
def then_audit_reports(
    cli_invocation: dict[str, RunResult], rule_id: str, message: str
) -> None:
    """Assert the rendered table carries a finding with this rule and text."""
    stdout = re.sub(r"\s+", " ", cli_invocation["result"].stdout)
    assert rule_id in stdout, stdout
    assert message in stdout, stdout
