"""Behavioural tests for `concordat artefact rule run` on UV-001 to UV-007.

These scenarios run the real policy over a real Makefile (parsed by the pinned
`makeutil`), a real copy of the canonical helper and a real `pyproject.toml`
through `cli.main`, so they cover the whole boundary an operator meets: a
checkout on disk, the envelope built from it, Conftest's verdict, the process
exit status and the rendered table. The Rego suite proves the clauses; this
proves the command delivers them.
"""

from __future__ import annotations

import pathlib
import re
import shutil
import typing as typ

import pytest
from pytest_bdd import given, parsers, scenarios, then, when
from ruamel.yaml import YAML

from concordat import cli

from .conftest import RunResult

scenarios("features/uv_gate_baseline_rule_run.feature")

RULE_ID: typ.Final = "uv-gate-baseline"
SPELLING_RULE_ID: typ.Final = "spelling-config-baseline"
_SPELLING_MANIFEST: typ.Final = (
    pathlib.Path(__file__).resolve().parents[2]
    / "platform-standards/canon/lint-rules/spelling-config-baseline/rule.yaml"
)
_SPELLING_GITIGNORE: typ.Final = (
    ".typos-oxendict-base.json\n.typos-oxendict-base.toml\n"
)
_SPELLING_RECIPE: typ.Final = (
    "\tuvx --from "
    '"git+https://github.com/leynos/typos-config-builder.git@v0.1.3" '
    "typos-config-builder gate --scope all"
)
_REPOSITORY: typ.Final = pathlib.Path(__file__).resolve().parents[2]
_CANONICAL_GATE: typ.Final = _REPOSITORY / "tests/fixtures/uv_gate/uv_gate.py.canon"
_MAKEFILE: typ.Final = """\
UV_GATE ?= python3 scripts/uv_gate.py

.PHONY: prepare lint
prepare:
\t$(UV_GATE) prepare --group dev

lint: prepare
\t{lint}
"""
_GATED_LINT: typ.Final = "$(UV_GATE) run --group dev -- ruff check ."
_PYPROJECT: typ.Final = '[project]\nname = "demo"\nversion = "0.1.0"\n'


@pytest.fixture
def uv_checkout(tmp_path: pathlib.Path) -> pathlib.Path:
    """Provide the checkout directory one scenario audits."""
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "README.md").write_text("# Checkout\n", encoding="utf-8")
    return checkout


def _write_checkout(
    checkout: pathlib.Path,
    *,
    lint: str = _GATED_LINT,
    gate: pathlib.Path = _CANONICAL_GATE,
    lock: bool = True,
) -> None:
    """Write a Makefile, the helper, a project file and, optionally, its lock."""
    (checkout / "Makefile").write_text(_MAKEFILE.format(lint=lint), encoding="utf-8")
    (checkout / "scripts").mkdir()
    shutil.copyfile(gate, checkout / "scripts" / "uv_gate.py")
    (checkout / "pyproject.toml").write_text(_PYPROJECT, encoding="utf-8")
    if lock:
        (checkout / "uv.lock").write_text("version = 1\n", encoding="utf-8")


@given("a checkout that does not use uv")
def given_no_uv(uv_checkout: pathlib.Path) -> None:
    """Leave the checkout with prose and a Makefile that never mentions uv."""
    (uv_checkout / "Makefile").write_text("build:\n\tcc -o x x.c\n", encoding="utf-8")


@given("a checkout that runs uv only through the canonical helper")
def given_canonical(uv_checkout: pathlib.Path) -> None:
    """Write the reference wiring with the canonical helper."""
    _write_checkout(uv_checkout)


@given("a checkout whose lint recipe runs uv directly")
def given_bypass(uv_checkout: pathlib.Path) -> None:
    """Write a lint recipe that calls uv itself."""
    _write_checkout(uv_checkout, lint="uv run --group dev ruff check .")


@given("a checkout with an edited helper and a cache override")
def given_edited_helper(uv_checkout: pathlib.Path, tmp_path: pathlib.Path) -> None:
    """Vendor a hand-edited helper and set the cache in the Makefile."""
    edited = tmp_path / "edited_uv_gate.py"
    edited.write_text(
        _CANONICAL_GATE.read_text(encoding="utf-8") + "\n# edit\n", encoding="utf-8"
    )
    _write_checkout(uv_checkout, gate=edited)
    makefile = uv_checkout / "Makefile"
    makefile.write_text(
        "UV_CACHE_DIR := .uv-cache\n" + makefile.read_text(encoding="utf-8"),
        encoding="utf-8",
    )


@given("a checkout with an unpinned tool and no lock")
def given_unpinned_no_lock(uv_checkout: pathlib.Path) -> None:
    """Run a tool without a version and leave out uv.lock."""
    _write_checkout(
        uv_checkout, lint="$(UV_GATE) tool --from ruff -- ruff check .", lock=False
    )


def _spelling_agents_md() -> str:
    """Return an AGENTS.md carrying the spelling manifest's newest block."""
    manifest = YAML(typ="safe").load(_SPELLING_MANIFEST.read_text(encoding="utf-8"))
    blocks = manifest["parameters"]["defaults"]["agents_md_blocks"]

    def key(tag: str) -> tuple[int, ...]:
        return tuple(int(part) for part in tag.removeprefix("v").split("."))

    body = blocks[max(blocks, key=key)]
    return (
        "# Agents\n\n<!-- typos-config-builder:agents-md:start -->\n\n"
        f"{body}\n<!-- typos-config-builder:agents-md:end -->\n"
    )


@given("a checkout with the canonical helper and the pinned spelling gate")
def given_both_rules(uv_checkout: pathlib.Path) -> None:
    """Write one checkout that both rule packages should accept."""
    _write_checkout(uv_checkout)
    makefile = uv_checkout / "Makefile"
    makefile.write_text(
        makefile.read_text(encoding="utf-8")
        + f"\n.PHONY: spelling\nspelling:\n{_SPELLING_RECIPE}\n",
        encoding="utf-8",
    )
    (uv_checkout / "typos.local.toml").write_text("schema = 1\n", encoding="utf-8")
    (uv_checkout / ".gitignore").write_text(_SPELLING_GITIGNORE, encoding="utf-8")
    (uv_checkout / "AGENTS.md").write_text(_spelling_agents_md(), encoding="utf-8")


def _audit(
    checkout: pathlib.Path,
    cli_invocation: dict[str, RunResult],
    capsys: pytest.CaptureFixture[str],
    *,
    rule_id: str = RULE_ID,
    key: str = "result",
) -> None:
    """Invoke the command on *checkout* and record its output and exit status."""
    try:
        returncode = cli.main(
            ["artefact", "rule", "run", rule_id, "--repo", str(checkout)],
        )
    except SystemExit as exc:
        returncode = int(exc.code or 0)
    captured = capsys.readouterr()
    cli_invocation[key] = RunResult(
        stdout=captured.out,
        stderr=captured.err,
        returncode=returncode,
    )


@when("I audit the same checkout for its spelling gate")
def when_audit_spelling(
    uv_checkout: pathlib.Path,
    cli_invocation: dict[str, RunResult],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Audit the scenario's checkout against the spelling baseline too."""
    _audit(
        uv_checkout,
        cli_invocation,
        capsys,
        rule_id=SPELLING_RULE_ID,
        key="spelling",
    )


@then(parsers.cfparse("the uv gate audit exit status is {code:d}"))
def then_uv_exit_status(cli_invocation: dict[str, RunResult], code: int) -> None:
    """Assert the uv gate audit's exit status."""
    result = cli_invocation["result"]
    assert result.returncode == code, result.stderr or result.stdout


@then(parsers.cfparse("the spelling audit exit status is {code:d}"))
def then_spelling_exit_status(cli_invocation: dict[str, RunResult], code: int) -> None:
    """Assert the spelling audit's exit status."""
    result = cli_invocation["spelling"]
    assert result.returncode == code, result.stderr or result.stdout


@when("I audit the checkout for its uv gate")
def when_audit_checkout(
    uv_checkout: pathlib.Path,
    cli_invocation: dict[str, RunResult],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Audit the scenario's checkout."""
    _audit(uv_checkout, cli_invocation, capsys)


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
    assert "UV-0" not in stdout, stdout


@then(parsers.cfparse('the audit output reports "{rule_id}" "{message}"'))
def then_audit_reports(
    cli_invocation: dict[str, RunResult], rule_id: str, message: str
) -> None:
    """Assert the rendered table carries one finding with this rule and text."""
    stdout = cli_invocation["result"].stdout
    assert any(rule_id in line and message in line for line in stdout.splitlines()), (
        stdout
    )
