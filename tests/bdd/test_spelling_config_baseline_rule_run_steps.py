"""Behavioural tests for `concordat artefact rule run` on PD-007 to PD-013.

These scenarios run the real policy over a real Makefile (parsed by the
pinned `makeutil`), overlay and `.gitignore` through `cli.main`, so they cover
the whole boundary an operator meets: a checkout on disk, the envelope built
from it, Conftest's verdict, the process exit status and the rendered table.
The Rego suite proves the clauses; this proves the command delivers them.
"""

from __future__ import annotations

import pathlib
import re
import typing as typ

import pytest
from pytest_bdd import given, parsers, scenarios, then, when
from ruamel.yaml import YAML

from concordat import cli

from .conftest import RunResult

scenarios("features/spelling_config_baseline_rule_run.feature")

RULE_ID: typ.Final = "spelling-config-baseline"
_BUILDER: typ.Final = "git+https://github.com/leynos/typos-config-builder.git"
_GATE_MAKEFILE: typ.Final = """\
.PHONY: spelling
spelling:
\tuvx --from "{builder}@{release}" typos-config-builder gate --scope all
"""
_LEGACY_MAKEFILE: typ.Final = """\
TYPOS_VERSION ?= 1.48.0
TYPOS_CONFIG = uvx --from "{builder}@v0.1.2" typos-config-builder
.PHONY: spelling
spelling:
\t$(TYPOS_CONFIG) --repository . --check
"""
_GITIGNORE: typ.Final = ".typos-oxendict-base.json\n.typos-oxendict-base.toml\n"
_REPOSITORY: typ.Final = pathlib.Path(__file__).resolve().parents[2]
_MANIFEST: typ.Final = (
    _REPOSITORY
    / "platform-standards/canon/lint-rules/spelling-config-baseline/rule.yaml"
)


def _release_key(tag: str) -> tuple[int, ...]:
    """Order release tags numerically, so ``v0.10.0`` follows ``v0.9.0``."""
    return tuple(int(part) for part in tag.removeprefix("v").split("."))


def _agents_md() -> str:
    """Return an AGENTS.md carrying the manifest's newest spelling block."""
    manifest = YAML(typ="safe").load(_MANIFEST.read_text(encoding="utf-8"))
    blocks = manifest["parameters"]["defaults"]["agents_md_blocks"]
    body = blocks[max(blocks, key=_release_key)]
    return (
        "# Agents\n\n<!-- typos-config-builder:agents-md:start -->\n\n"
        f"{body}\n<!-- typos-config-builder:agents-md:end -->\n"
    )


@pytest.fixture
def spelling_checkout(tmp_path: pathlib.Path) -> pathlib.Path:
    """Provide the checkout directory one scenario audits."""
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "README.md").write_text("# Checkout\n", encoding="utf-8")
    return checkout


def _write_setup(checkout: pathlib.Path, makefile: str) -> None:
    """Write a Makefile, a schema 1 overlay and the cache's ignore lines."""
    (checkout / "Makefile").write_text(makefile, encoding="utf-8")
    (checkout / "typos.local.toml").write_text("schema = 1\n", encoding="utf-8")
    (checkout / ".gitignore").write_text(_GITIGNORE, encoding="utf-8")
    (checkout / "AGENTS.md").write_text(_agents_md(), encoding="utf-8")


@given("a checkout with no spelling setup")
def given_no_setup(spelling_checkout: pathlib.Path) -> None:
    """Leave the checkout with prose but no spelling gate."""


@given("a checkout whose spelling target runs the pinned gate")
def given_pinned_gate(spelling_checkout: pathlib.Path) -> None:
    """Write the canonical gate at the floor release."""
    _write_setup(
        spelling_checkout, _GATE_MAKEFILE.format(builder=_BUILDER, release="v0.1.2")
    )


@given("a checkout whose spelling target pins v0.1.1")
def given_below_floor(spelling_checkout: pathlib.Path) -> None:
    """Write the gate pinned to a release below the floor."""
    _write_setup(
        spelling_checkout, _GATE_MAKEFILE.format(builder=_BUILDER, release="v0.1.1")
    )


@given("a checkout with the legacy drift-check setup")
def given_legacy_setup(spelling_checkout: pathlib.Path) -> None:
    """Write the statelet-era shape: a Typos pin, --check, a vendored script."""
    _write_setup(spelling_checkout, _LEGACY_MAKEFILE.format(builder=_BUILDER))
    scripts = spelling_checkout / "scripts"
    scripts.mkdir()
    (scripts / "typos_rollout_check.py").write_text('"""Legacy."""\n', encoding="utf-8")


def _audit(
    checkout: pathlib.Path,
    cli_invocation: dict[str, RunResult],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Invoke the command on *checkout* and record its output and exit status."""
    try:
        returncode = cli.main(
            ["artefact", "rule", "run", RULE_ID, "--repo", str(checkout)],
        )
    except SystemExit as exc:
        returncode = int(exc.code or 0)
    captured = capsys.readouterr()
    cli_invocation["result"] = RunResult(
        stdout=captured.out,
        stderr=captured.err,
        returncode=returncode,
    )


@when("I audit the checkout for its spelling gate")
def when_audit_checkout(
    spelling_checkout: pathlib.Path,
    cli_invocation: dict[str, RunResult],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Audit the scenario's checkout."""
    _audit(spelling_checkout, cli_invocation, capsys)


@when("I audit concordat's own checkout for its spelling gate")
def when_audit_own_checkout(
    cli_invocation: dict[str, RunResult],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Audit this repository, so its Makefile pin and AGENTS.md block conform."""
    _audit(_REPOSITORY, cli_invocation, capsys)


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
    assert "PD-0" not in stdout, stdout


@then(parsers.cfparse('the audit output reports "{rule_id}" "{message}"'))
def then_audit_reports(
    cli_invocation: dict[str, RunResult], rule_id: str, message: str
) -> None:
    """Assert the rendered table carries one finding with this rule and text."""
    stdout = cli_invocation["result"].stdout
    assert any(rule_id in line and message in line for line in stdout.splitlines()), (
        stdout
    )
