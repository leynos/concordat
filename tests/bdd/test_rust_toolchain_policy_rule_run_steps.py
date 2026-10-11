"""Behavioural tests for `concordat artefact rule run` on RT-022 to RT-027.

These scenarios run the real policy over a real checkout through `cli.main`,
so they cover the whole boundary an operator meets: files on disk, the envelope
built from them, Conftest's verdict, the exit status and the rendered table.
The Rego suite proves the clauses; this proves the command delivers them.
"""

from __future__ import annotations

import datetime as dt
import re
import typing as typ

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from concordat import cli

from .conftest import RunResult

if typ.TYPE_CHECKING:
    import pathlib

scenarios("features/rust_toolchain_policy_rule_run.feature")

RULE_ID: typ.Final = "rust-toolchain-policy"

_MANIFEST: typ.Final = '[package]\nname = "demo"\nversion = "0.1.0"\n'
_DEPENDABOT: typ.Final = (
    "version: 2\nupdates:\n  - package-ecosystem: rust-toolchain\n    directory: /\n"
    "    schedule:\n      interval: daily\n"
)


def _nightly(days_old: int) -> str:
    """Return the dated nightly channel `days_old` days before today (UTC)."""
    today = dt.datetime.now(dt.UTC).date()
    return f"nightly-{today - dt.timedelta(days=days_old):%Y-%m-%d}"


def _pin(channel: str) -> str:
    """Render a `rust-toolchain.toml` pinning *channel*."""
    return f'[toolchain]\nchannel = "{channel}"\n'


@pytest.fixture
def toolchain_checkout(tmp_path: pathlib.Path) -> pathlib.Path:
    """Provide the checkout directory one scenario audits."""
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    return checkout


def _write(checkout: pathlib.Path, relative: str, text: str) -> None:
    """Write *text* at *relative* under *checkout*."""
    path = checkout / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@given("a checkout with no Rust code")
def given_no_rust(toolchain_checkout: pathlib.Path) -> None:
    """Leave the checkout without a Cargo manifest."""


@given("a checkout pinned to a fresh nightly that Dependabot moves")
def given_fresh_pin(toolchain_checkout: pathlib.Path) -> None:
    """Write a recent pin, a Dependabot entry and an application manifest."""
    _write(toolchain_checkout, "Cargo.toml", _MANIFEST)
    _write(toolchain_checkout, "rust-toolchain.toml", _pin(_nightly(7)))
    _write(toolchain_checkout, ".github/dependabot.yml", _DEPENDABOT)


@given("a checkout pinned to an old nightly with no Dependabot entry")
def given_old_pin(toolchain_checkout: pathlib.Path) -> None:
    """Write a pin far outside the window and nothing to move it."""
    _write(toolchain_checkout, "Cargo.toml", _MANIFEST)
    _write(toolchain_checkout, "rust-toolchain.toml", _pin(_nightly(200)))


@given("a checkout pinned to the floating stable channel")
def given_floating_pin(toolchain_checkout: pathlib.Path) -> None:
    """Write a floating pin, with Dependabot configured so only RT-022 fires."""
    _write(toolchain_checkout, "Cargo.toml", _MANIFEST)
    _write(toolchain_checkout, "rust-toolchain.toml", _pin("stable"))
    _write(toolchain_checkout, ".github/dependabot.yml", _DEPENDABOT)


@given("a checkout declaring an MSRV that no workflow exercises")
def given_unexercised_msrv(toolchain_checkout: pathlib.Path) -> None:
    """Write a library with rust-version 1.85 and a workflow that never uses it."""
    _write(toolchain_checkout, "Cargo.toml", f'{_MANIFEST}rust-version = "1.85"\n')
    _write(toolchain_checkout, "src/lib.rs", "")
    _write(toolchain_checkout, "rust-toolchain.toml", _pin("1.93.1"))
    _write(toolchain_checkout, ".github/dependabot.yml", _DEPENDABOT)
    _write(
        toolchain_checkout,
        ".github/workflows/ci.yml",
        "jobs:\n  test:\n    steps:\n      - run: cargo test\n",
    )


def _msrv_checkout(checkout: pathlib.Path, action: str) -> None:
    """Write a library declaring rust-version 1.85 whose one job uses *action*."""
    _write(checkout, "Cargo.toml", f'{_MANIFEST}rust-version = "1.85"\n')
    _write(checkout, "src/lib.rs", "")
    _write(checkout, "rust-toolchain.toml", _pin("1.93.1"))
    _write(checkout, ".github/dependabot.yml", _DEPENDABOT)
    _write(
        checkout,
        ".github/workflows/ci.yml",
        f"jobs:\n  check:\n    steps:\n      - uses: {action}\n",
    )


@given("a checkout declaring an MSRV and a job that uses the shared msrv-check action")
def given_shared_msrv_action(toolchain_checkout: pathlib.Path) -> None:
    """Write a library whose MSRV job calls the shared action."""
    _msrv_checkout(
        toolchain_checkout,
        "leynos/shared-actions/.github/actions/msrv-check@" + "a" * 40,
    )


@given(
    "a checkout declaring an MSRV and a job that uses a lookalike of the "
    "msrv-check action"
)
def given_lookalike_msrv_action(toolchain_checkout: pathlib.Path) -> None:
    """Write a library whose job calls an action that only starts like it."""
    _msrv_checkout(
        toolchain_checkout,
        "leynos/shared-actions/.github/actions/msrv-check-lookalike@v1",
    )


@given("a checkout with an old nightly and a toolchain policy exception naming it")
def given_excepted_pin(toolchain_checkout: pathlib.Path) -> None:
    """Write an old pin whose developers' guide records a current exception."""
    channel = _nightly(200)
    _write(toolchain_checkout, "Cargo.toml", _MANIFEST)
    _write(toolchain_checkout, "rust-toolchain.toml", _pin(channel))
    _write(
        toolchain_checkout,
        "docs/developers-guide.md",
        f"# Guide\n\n## Toolchain policy exception\n\n"
        f"The toolchain {channel} is coupled to a vendored compiler crate.\n",
    )


@when("I audit the checkout for its toolchain policy")
def when_audit_checkout(
    toolchain_checkout: pathlib.Path,
    cli_invocation: dict[str, RunResult],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Invoke the command and record its output and exit status."""
    try:
        returncode = cli.main(
            ["artefact", "rule", "run", RULE_ID, "--repo", str(toolchain_checkout)],
        )
    except SystemExit as exc:
        returncode = int(exc.code or 0)
    captured = capsys.readouterr()
    cli_invocation["result"] = RunResult(
        stdout=captured.out,
        stderr=captured.err,
        returncode=returncode,
    )


@then(parsers.cfparse("the toolchain audit exit status is {code:d}"))
def then_audit_exit_status(cli_invocation: dict[str, RunResult], code: int) -> None:
    """Assert the recorded exit status."""
    result = cli_invocation["result"]
    assert result.returncode == code, result.stderr or result.stdout


@then("the toolchain audit table reports zero findings")
def then_audit_clean(cli_invocation: dict[str, RunResult]) -> None:
    """Assert the table names the compliant verdict and no findings."""
    stdout = cli_invocation["result"].stdout
    assert re.search(r"\bcompliant\b", stdout), stdout
    assert not re.search(r"RT-02[2-7]", stdout), stdout


@then(parsers.cfparse('the toolchain audit output reports "{rule}" and "{message}"'))
def then_audit_reports(
    cli_invocation: dict[str, RunResult], rule: str, message: str
) -> None:
    """Assert the rendered finding carries its rule and message."""
    stdout = cli_invocation["result"].stdout
    assert rule in stdout, stdout
    assert message in stdout, stdout
