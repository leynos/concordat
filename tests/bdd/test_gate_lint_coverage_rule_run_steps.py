"""Behavioural tests for `concordat artefact rule run` on gate-lint-coverage.

These scenarios run the real policy over a real Makefile, manifests and scripts
through `cli.main`, so they cover the whole boundary an operator meets: a
checkout on disk, the envelope built from it, Conftest's verdict, the process
exit status and the rendered table. The Rego suite proves the clauses; this
proves the command delivers them, for a workspace designed as one, one grown from
a single crate and a Python project's extension.
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

scenarios("features/gate_lint_coverage_rule_run.feature")

RULE_ID: typ.Final = "gate-lint-coverage"
DENY: typ.Final = 'RUSTFLAGS="-D warnings"'
DENY_DOC: typ.Final = 'RUSTDOCFLAGS="-D warnings"'
CLIPPY: typ.Final = f"{DENY} cargo clippy --workspace --all-targets --all-features"
DOC: typ.Final = f"{DENY_DOC} cargo doc --workspace --no-deps"
EXT: typ.Final = "--manifest-path rust_extension/Cargo.toml"
FEATURES: typ.Final = '\n[features]\ndefault = ["fast"]\nfast = []\nslow = []\n'
WORKSPACE: typ.Final = '[workspace]\nmembers = ["crates/*"]\nresolver = "3"\n'


def _package(name: str, extra: str = "") -> str:
    """Return a `[package]` table followed by *extra*."""
    return f'[package]\nname = "{name}"\nversion = "0.1.0"\n{extra}'


def _makefile(*lint: str) -> str:
    """Return a Makefile whose `lint` target runs the given commands."""
    body = "".join(f"\t{command}\n" for command in lint)
    return f".PHONY: lint\nlint:\n{body}"


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


def _workspace(checkout: pathlib.Path, *lint: str) -> None:
    """Write a virtual workspace (a library with two features, and a member)."""
    _write(
        checkout,
        {
            "Cargo.toml": WORKSPACE,
            "crates/a/Cargo.toml": _package("a", FEATURES),
            "crates/a/src/lib.rs": "",
            "crates/b/Cargo.toml": _package("b"),
            "crates/b/src/lib.rs": "",
            "Makefile": _makefile(*lint),
        },
    )


@given("a checkout with no Cargo manifest")
def given_no_manifest(rust_checkout: pathlib.Path) -> None:
    """Leave a checkout that holds only prose."""
    _write(rust_checkout, {"README.md": "# Demo\n"})


@given(
    "a workspace whose lint target checks every target and feature and builds the docs"
)
def given_clean(rust_checkout: pathlib.Path) -> None:
    """Write a gate that checks all targets and features, denies warnings, and docs."""
    _workspace(rust_checkout, CLIPPY, DOC)


@given("a workspace whose clippy leaves out the test targets")
def given_no_all_targets(rust_checkout: pathlib.Path) -> None:
    """Write a Clippy run without `--all-targets`."""
    _workspace(rust_checkout, f"{DENY} cargo clippy --workspace --all-features", DOC)


@given("a workspace whose clippy checks only one of two features")
def given_one_feature(rust_checkout: pathlib.Path) -> None:
    """Write one feature lane, so `slow` is never checked."""
    _workspace(
        rust_checkout,
        f"{DENY} cargo clippy --workspace --all-targets --features fast",
        DOC,
    )


@given("a workspace whose clippy does not deny warnings")
def given_warnings(rust_checkout: pathlib.Path) -> None:
    """Write a Clippy run with no `-D warnings` anywhere."""
    _workspace(
        rust_checkout, "cargo clippy --workspace --all-targets --all-features", DOC
    )


@given("a root package with a member whose gate never runs cargo doc")
def given_no_doc(rust_checkout: pathlib.Path) -> None:
    """Write a grown workspace whose gate has Clippy and nothing else."""
    _write(
        rust_checkout,
        {
            "Cargo.toml": _package("app", FEATURES)
            + '\n[workspace]\nmembers = ["m"]\n',
            "src/lib.rs": "",
            "m/Cargo.toml": _package("m"),
            "m/src/lib.rs": "",
            "Makefile": _makefile(CLIPPY),
        },
    )


def _extension(
    checkout: pathlib.Path, *lint: str, extra: dict[str, str] | None = None
) -> None:
    """Write a maturin project whose `rust_extension` crate is the surface."""
    _write(
        checkout,
        {
            "pyproject.toml": '[project]\nname = "demo"\n',
            "rust_extension/Cargo.toml": _package("ext", FEATURES),
            "rust_extension/src/lib.rs": "",
            "Makefile": _makefile(*lint),
            **(extra or {}),
        },
    )


@given(
    "a Python project whose rust_extension crate is linted and documented by "
    "manifest path"
)
def given_extension_clean(rust_checkout: pathlib.Path) -> None:
    """Write femtologging's shape with every command naming the manifest."""
    _extension(
        rust_checkout,
        f"{DENY} cargo clippy {EXT} --all-targets --all-features",
        f"{DENY_DOC} cargo doc {EXT} --no-deps",
    )


@given("a Python project whose lint lanes are built by a script")
def given_extension_script(rust_checkout: pathlib.Path) -> None:
    """Write femtologging's real gap: lanes from a script, and no cargo doc."""
    _extension(
        rust_checkout,
        'INPUT_LANES="none all" uv run --script scripts/lint_rust_lanes.py',
        extra={
            "scripts/lint_rust_lanes.py": (
                '"""Lint lanes."""\nimport subprocess\n'
                'subprocess.run(["cargo", "clippy", "--all-targets"], check=True)\n'
            )
        },
    )


@when("I audit the checkout for gate lint coverage")
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
    assert "LC-0" not in stdout, stdout


@then(parsers.cfparse('the audit output reports "{rule_id}" "{message}"'))
def then_audit_reports(
    cli_invocation: dict[str, RunResult], rule_id: str, message: str
) -> None:
    """Assert the rendered table carries a finding with this rule and text."""
    stdout = re.sub(r"\s+", " ", cli_invocation["result"].stdout)
    assert rule_id in stdout, stdout
    assert message in stdout, stdout
