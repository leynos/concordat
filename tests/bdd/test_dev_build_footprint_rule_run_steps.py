"""Exercise the development-footprint rule through the actual operator CLI."""

from __future__ import annotations

import typing as typ

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from concordat import cli

from .conftest import RunResult

if typ.TYPE_CHECKING:
    import pathlib

scenarios("features/dev_build_footprint_rule_run.feature")


@pytest.fixture
def footprint_checkout(tmp_path: pathlib.Path) -> pathlib.Path:
    """Return the scenario's isolated checkout directory."""
    return tmp_path


@given(parsers.parse('a development checkout with "{profile}" settings on "{channel}"'))
def given_dev_settings(
    footprint_checkout: pathlib.Path, profile: str, channel: str
) -> None:
    """Create synthetic repository inputs independent of the host toolchain."""
    settings = (
        "[profile.dev]\nincremental = false\ndebug = 0\n" if profile == "small" else ""
    )
    (footprint_checkout / "Cargo.toml").write_text(
        '[package]\nname = "demo"\nversion = "0.1.0"\n' + settings,
        encoding="utf-8",
    )
    (footprint_checkout / "rust-toolchain.toml").write_text(
        f'[toolchain]\nchannel = "{channel}"\n', encoding="utf-8"
    )


@given(parsers.parse('its configuration "{metadata}" the metadata flag'))
def given_metadata_flag(footprint_checkout: pathlib.Path, metadata: str) -> None:
    """Write the actual flag, or leave the Cargo configuration empty."""
    path = footprint_checkout / ".cargo/config.toml"
    path.parent.mkdir()
    path.write_text(
        '[build]\nrustflags = ["-Zembed-metadata=no"]\n'
        if metadata == "includes"
        else "",
        encoding="utf-8",
    )


@when("I audit the development build footprint")
def when_audit_footprint(
    footprint_checkout: pathlib.Path,
    cli_invocation: dict[str, RunResult],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Record the real CLI's table, diagnostics and exit status."""
    try:
        code = cli.main([
            "artefact",
            "rule",
            "run",
            "rust-dev-build-footprint",
            "--repo",
            str(footprint_checkout),
        ])
    except SystemExit as error:
        code = int(error.code or 0)
    captured = capsys.readouterr()
    cli_invocation["result"] = RunResult(
        stdout=captured.out, stderr=captured.err, returncode=code
    )


@then(parsers.cfparse("the footprint exit status is {status:d}"))
def then_footprint_status(cli_invocation: dict[str, RunResult], status: int) -> None:
    """Assert the exit status presented to an operator or CI job."""
    result = cli_invocation["result"]
    assert result.returncode == status, result.stderr or result.stdout


@then(parsers.parse('the footprint output contains "{finding}"'))
def then_footprint_output(cli_invocation: dict[str, RunResult], finding: str) -> None:
    """Assert the actual rendered verdict or rule identifier."""
    output = cli_invocation["result"].stdout
    assert finding in output, output
