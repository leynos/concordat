"""Contract tests for Skylos dead-code detection in Make and CI.

The scanner accepts ``--config-file`` before paths, but its ``whitelist``
subcommand must appear immediately after ``skylos``. Skylos uses its own Python
AST, so it must run with Python 3.14. Makeutil supplies structured Makefile
facts, avoiding brittle whitespace or substring assertions.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import tomllib
import typing as typ
from pathlib import Path
from tempfile import TemporaryDirectory

from hypothesis import example, given, settings
from hypothesis import strategies as st
from ruamel.yaml import YAML

REPOSITORY_ROOT = Path(__file__).parents[2]
_MAKEUTIL_COMMAND: typ.Final = ("makeutil", "parse", "Makefile")
_MAKEUTIL_REVISION: typ.Final = "29fc5a1634ffbaa18a773eed9dff1b2838a45d9c"
_MAKEUTIL_TOOLCHAIN: typ.Final = "nightly-2026-05-28"
_MAKEUTIL_INSTALL_TOKENS: typ.Final = (
    "rustup",
    "toolchain",
    "install",
    "${MAKEUTIL_TOOLCHAIN}",
    "--profile",
    "minimal",
    "RUSTFLAGS=-Zpolonius=next",
    "cargo",
    "+${MAKEUTIL_TOOLCHAIN}",
    "install",
    "--git",
    "https://github.com/leynos/makeutil",
    "--rev",
    "${MAKEUTIL_REVISION}",
    "--locked",
    "--force",
    "makeutil",
)
_TEXTUAL_ACTIONS: typ.Final = frozenset(
    {
        "scripts.canon_artifacts_tui.CanonArtifactsApp.action_refresh",
        "scripts.canon_artifacts_tui.CanonArtifactsApp.action_sync_selected",
        "scripts.canon_artifacts_tui.CanonArtifactsApp.action_sync_all_outdated",
    }
)
_TEXTUAL_BINDINGS: typ.Final = frozenset(
    {
        "scripts.canon_artifacts_tui.CanonArtifactsApp.BINDINGS",
    }
)
_DOCUMENTED_FALSE_POSITIVES: typ.Final = frozenset(
    {
        "RefreshResult",
        "_format_outcome",
        "_refresh",
        "published_path",
        "render",
        "template_path",
    }
)
_SKYLOS_ARGUMENT_TEXT: typ.Final = st.text(
    alphabet=(
        "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 "
        "\t$;&|*?[]{}()<>!\\\"'`"
    ),
    min_size=1,
    max_size=24,
)


def _make_executable() -> str:
    """Return the resolved Make executable required by the boundary tests."""
    executable = shutil.which("make")
    assert executable is not None, "Skylos contract tests require make on PATH"
    return executable


_MAKE_EXECUTABLE: typ.Final = _make_executable()


def _makefile_report() -> dict[str, object]:
    """Return Makeutil's complete, successfully parsed Makefile report."""
    completed = subprocess.run(  # noqa: S603 - Fixed parser command.
        _MAKEUTIL_COMMAND,
        capture_output=True,
        check=True,
        cwd=REPOSITORY_ROOT,
        text=True,
    )
    report = typ.cast("dict[str, object]", json.loads(completed.stdout))
    parse = _mapping(report.get("parse"), subject="parse report")
    assert parse.get("status") == "complete", (
        f"makeutil did not complete the Makefile parse: {parse!r}"
    )
    return report


def _mapping(value: object, *, subject: str) -> dict[str, object]:
    """Return a JSON object, naming the unexpected ``subject`` on failure."""
    assert isinstance(value, dict), f"expected {subject} to be a JSON object"
    return typ.cast("dict[str, object]", value)


def _objects(value: object, *, subject: str) -> list[dict[str, object]]:
    """Return a JSON-object array, naming the unexpected ``subject``."""
    assert isinstance(value, list), f"expected {subject} to be a JSON array"
    return [_mapping(item, subject=f"{subject} item") for item in value]


def _text_sequence(value: object, *, subject: str) -> tuple[str, ...]:
    """Return a JSON string array, naming the unexpected ``subject``."""
    assert isinstance(value, list), f"expected {subject} to be a JSON array"
    assert all(isinstance(item, str) for item in value), (
        f"expected {subject} to contain only JSON strings"
    )
    return tuple(typ.cast("list[str]", value))


def _text_values(value: object, *, subject: str) -> tuple[str, ...]:
    """Return one or more TOML strings, naming malformed input."""
    if isinstance(value, str):
        return (value,)
    return _text_sequence(value, subject=subject)


def _sole_variable(name: str) -> dict[str, object]:
    """Return Makeutil's sole variable fact for ``name``."""
    variables = _objects(_makefile_report().get("variables"), subject="variables")
    matches = [variable for variable in variables if variable.get("name") == name]
    assert len(matches) == 1, (
        f"expected one Makefile variable named {name!r}, found {len(matches)}"
    )
    return matches[0]


def _sole_recipe_rule(target: str) -> dict[str, object]:
    """Return the only parsed recipe-bearing rule for ``target``."""
    rules = _objects(_makefile_report().get("rules"), subject="rules")
    matches = [
        rule
        for rule in rules
        if target in _text_sequence(rule.get("targets"), subject="rule targets")
        and _objects(rule.get("recipes"), subject="rule recipes")
    ]
    assert len(matches) == 1, (
        f"expected one recipe-bearing Makefile rule named {target!r}, found "
        f"{len(matches)}"
    )
    return matches[0]


def _variable_tokens(name: str) -> tuple[str, ...]:
    """Return shell-like tokens from Makeutil's raw variable value."""
    value = _sole_variable(name).get("raw_value")
    assert isinstance(value, str), f"expected {name!r} to have a string value"
    return tuple(shlex.split(value))


def _recipe_tokens(target: str) -> tuple[tuple[str, ...], ...]:
    """Return shell-like tokens for every recipe in ``target``."""
    recipes = _objects(
        _sole_recipe_rule(target).get("recipes"), subject=f"{target} recipes"
    )
    return tuple(
        tuple(shlex.split(recipe_text))
        for recipe in recipes
        if isinstance(recipe_text := recipe.get("text"), str)
    )


def _workflow_job(workflow_path: str, job_name: str) -> dict[str, object]:
    """Return the named job from a repository workflow."""
    yaml = YAML(typ="safe")
    workflow = _mapping(
        yaml.load((REPOSITORY_ROOT / workflow_path).read_text()),
        subject=f"{workflow_path} workflow",
    )
    jobs = _mapping(workflow.get("jobs"), subject=f"{workflow_path} jobs")
    return _mapping(jobs.get(job_name), subject=f"{workflow_path} job {job_name!r}")


def _sole_workflow_step(
    job_name: str, step_name: str, *, workflow_path: str = ".github/workflows/ci.yml"
) -> dict[str, object]:
    """Return the sole named workflow step from ``job_name``."""
    steps = _objects(
        _workflow_job(workflow_path, job_name).get("steps"),
        subject=f"{workflow_path} job {job_name!r} steps",
    )
    matches = [step for step in steps if step.get("name") == step_name]
    assert len(matches) == 1, (
        f"expected one {step_name!r} step in {workflow_path} job {job_name!r}, "
        f"found {len(matches)}"
    )
    return matches[0]


def _run_skylos_allow(*arguments: str) -> subprocess.CompletedProcess[str]:
    """Run the non-mutating whitelist validation boundary."""
    environment: dict[str, str] = dict(os.environ)
    environment["NAME"] = "wsl-hostname"
    environment.pop("REASON", None)
    environment.pop("SYMBOL", None)
    command: tuple[str, ...] = (_MAKE_EXECUTABLE, "skylos-allow", *arguments)
    return subprocess.run(  # noqa: S603 - Fixed Make target and arguments.
        command,
        capture_output=True,
        check=False,
        cwd=REPOSITORY_ROOT,
        env=environment,
        text=True,
    )


def _run_skylos_allow_with_stub(
    symbol: str, reason: str, directory: Path
) -> tuple[str, ...]:
    """Run the whitelist target against a non-mutating ``uv`` argument recorder."""
    arguments_path = directory / "arguments.json"
    uv_stub = directory / "uv"
    uv_stub.write_text(
        "#!/usr/bin/env python3\n"
        "import json\n"
        "import os\n"
        "import sys\n"
        "from pathlib import Path\n\n"
        'Path(os.environ["SKYLOS_ARGUMENTS_PATH"]).write_text(\n'
        "    json.dumps(sys.argv[1:]), encoding='utf-8'\n"
        ")\n",
        encoding="utf-8",
    )
    uv_stub.chmod(0o755)
    environment: dict[str, str] = dict(os.environ)
    environment["SKYLOS_ARGUMENTS_PATH"] = str(arguments_path)
    command: tuple[str, ...] = (
        _MAKE_EXECUTABLE,
        "--no-print-directory",
        f"UV_ENV=PATH={directory}:{environment['PATH']}",
        "skylos-allow",
        f"SYMBOL={symbol}",
        f"REASON={reason}",
    )
    completed = subprocess.run(  # noqa: S603 - Fixed Make target and temporary stub.
        command,
        capture_output=True,
        check=False,
        cwd=REPOSITORY_ROOT,
        env=environment,
        text=True,
    )
    assert completed.returncode == 0, (
        "Skylos whitelist property must accept non-empty generated arguments: "
        f"{completed.stderr}"
    )
    assert arguments_path.exists(), (
        "Skylos whitelist property must invoke the temporary uv recorder"
    )
    arguments = json.loads(arguments_path.read_text(encoding="utf-8"))
    return _text_sequence(arguments, subject="recorded Skylos arguments")


def _assert_makeutil_installation(command: object, *, contract: str) -> None:
    """Assert that ``command`` installs the pinned Makeutil parser."""
    assert isinstance(command, str), (
        f"{contract} must provide a Makeutil installation shell command"
    )
    assert (
        tuple(shlex.split(command.replace("\\\n", ""))) == _MAKEUTIL_INSTALL_TOKENS
    ), f"{contract} must pin the Makeutil toolchain, revision, and Polonius flag"


def _assert_makeutil_environment(job: dict[str, object], *, contract: str) -> None:
    """Assert that a full-suite job pins Makeutil's revision and toolchain."""
    environment = _mapping(job.get("env"), subject=f"{contract} environment")
    assert environment.get("MAKEUTIL_REVISION") == _MAKEUTIL_REVISION, (
        f"{contract} must pin Makeutil revision {_MAKEUTIL_REVISION}"
    )
    assert environment.get("MAKEUTIL_TOOLCHAIN") == _MAKEUTIL_TOOLCHAIN, (
        f"{contract} must pin Makeutil toolchain {_MAKEUTIL_TOOLCHAIN}"
    )


def test_lint_recipe_runs_the_production_dead_code_gate() -> None:
    """``make lint`` must scan only production code in strict gate mode."""
    assert _variable_tokens("SKYLOS_VERSION") == ("4.33.2",), (
        "Skylos version contract must pin 4.33.2"
    )
    assert _variable_tokens("SKYLOS_PRODUCTION_TARGETS") == ("concordat", "scripts"), (
        "Skylos production-target contract must scan concordat and scripts"
    )
    assert _variable_tokens("SKYLOS_EXCLUDE_FOLDERS") == ("tests",), (
        "Skylos exclusion contract must omit tests from production liveness"
    )
    commands = [
        command for command in _recipe_tokens("lint") if command[:1] == ("$(SKYLOS)",)
    ]
    assert commands == [
        (
            "$(SKYLOS)",
            "$(SKYLOS_PRODUCTION_TARGETS)",
            "--exclude",
            "$(SKYLOS_EXCLUDE_FOLDERS)",
            "--category",
            "dead_code",
            "--gate",
            "--format",
            "concise",
            "--no-upload",
            "--no-provenance",
            "--no-grep-verify",
        )
    ], "Skylos lint command must scan production dead code in strict gate mode"


def test_whitelist_target_uses_skylos_subcommand_contract() -> None:
    """The bare CLI must dispatch ``whitelist`` before its arguments."""
    assert _variable_tokens("SKYLOS_CLI") == (
        "$(UV_ENV)",
        "uv",
        "tool",
        "run",
        "--python",
        "3.14",
        "--from",
        "skylos==$(SKYLOS_VERSION)",
        "skylos",
    ), "Skylos CLI must pin Python 3.14 and the configured tool release"
    assert _variable_tokens("SKYLOS") == (
        "$(SKYLOS_CLI)",
        "--config-file",
        "pyproject.toml",
    ), "Skylos scan macro must contain scan-only configuration options"
    commands = [
        command
        for command in _recipe_tokens("skylos-allow")
        if command[:1] == ("$(SKYLOS_CLI)",)
    ]
    assert commands == [
        (
            "$(SKYLOS_CLI)",
            "whitelist",
            "$${SKYLOS_SYMBOL}",
            "--reason",
            "$${SKYLOS_REASON}",
        )
    ], "Skylos whitelist command must dispatch before SYMBOL and --reason"


def test_skylos_allow_requires_symbol_and_reason() -> None:
    """Incomplete whitelist input must fail before invoking Skylos."""
    for arguments, expected_error in (
        ((), "Error: SYMBOL is required for a named whitelist exception"),
        (
            ("SYMBOL=handler",),
            "Error: REASON is required for a named whitelist exception",
        ),
    ):
        completed = _run_skylos_allow(*arguments)
        assert completed.returncode == 2, (
            "Skylos whitelist boundary must reject missing required input with exit 2"
        )
        assert expected_error in completed.stderr, (
            "Skylos whitelist boundary must name the missing required input"
        )


def test_skylos_allow_dry_run_preserves_the_whitelist_command_contract() -> None:
    """A complete dry run must expose the command without writing an exception."""
    command: tuple[str, ...] = (
        _MAKE_EXECUTABLE,
        "--dry-run",
        "skylos-allow",
        "SYMBOL=handler",
        "REASON=Loaded by plugin registry",
    )
    completed = subprocess.run(  # noqa: S603 - Fixed dry-run Make command.
        command,
        capture_output=True,
        check=False,
        cwd=REPOSITORY_ROOT,
        text=True,
    )
    assert completed.returncode == 0, (
        "Skylos whitelist dry-run contract must accept complete input"
    )
    assert (
        'skylos whitelist "${SKYLOS_SYMBOL}" --reason "${SKYLOS_REASON}"'
        in completed.stdout
    ), "Skylos whitelist dry-run must preserve subcommand argument order"


@given(symbol=_SKYLOS_ARGUMENT_TEXT, reason=_SKYLOS_ARGUMENT_TEXT)
@example(symbol="symbol with spaces;$", reason="reason with $hell & 'quotes'")
@settings(max_examples=25, deadline=None)
def test_skylos_allow_forwards_non_empty_generated_arguments(
    symbol: str, reason: str
) -> None:
    """Every generated argument reaches Skylos as one whitelist argument."""
    with TemporaryDirectory() as temporary_directory:
        forwarded = _run_skylos_allow_with_stub(
            symbol, reason, Path(temporary_directory)
        )

    assert forwarded == (
        "tool",
        "run",
        "--python",
        "3.14",
        "--from",
        "skylos==4.33.2",
        "skylos",
        "whitelist",
        symbol,
        "--reason",
        reason,
    ), "Skylos whitelist property must forward each generated value exactly once"


def test_skylos_configuration_models_runtime_and_documented_boundaries() -> None:
    """Runtime callers need typed entries; remaining reports need reasons."""
    with (REPOSITORY_ROOT / "pyproject.toml").open("rb") as configuration_file:
        configuration = tomllib.load(configuration_file)
    tool = _mapping(configuration.get("tool"), subject="tool configuration")
    skylos = _mapping(tool.get("skylos"), subject="Skylos configuration")
    gate = _mapping(skylos.get("gate"), subject="Skylos gate configuration")
    assert gate.get("strict") is True, (
        "Skylos gate configuration must enable strict mode"
    )
    dead_code = _mapping(
        skylos.get("dead_code"), subject="Skylos dead-code configuration"
    )
    entry_points = _objects(dead_code.get("entrypoints"), subject="Skylos entry points")
    entry_point_sets = {
        (
            entry_point.get("type"),
            frozenset(
                _text_values(entry_point.get("full_name"), subject="entry-point name")
            ),
        )
        for entry_point in entry_points
    }
    assert entry_point_sets == {
        ("method", _TEXTUAL_ACTIONS),
        ("variable", _TEXTUAL_BINDINGS),
    }, "Skylos entry-point contract must preserve typed Textual runtime callers"
    for entry_point in entry_points:
        reason = entry_point.get("reason")
        assert isinstance(reason, str), (
            "Skylos entry-point contract must provide a textual runtime reason"
        )
        assert reason, "Skylos entry-point contract must provide a non-empty reason"
    whitelist = _mapping(skylos.get("whitelist"), subject="Skylos whitelist")
    documented = _mapping(
        whitelist.get("documented"), subject="documented Skylos whitelist"
    )
    assert frozenset(documented) == _DOCUMENTED_FALSE_POSITIVES, (
        "Skylos documented whitelist must contain only verified false positives"
    )
    for symbol, reason in documented.items():
        assert isinstance(reason, str), (
            f"Skylos documented whitelist entry {symbol!r} must include a reason"
        )
        assert reason, (
            f"Skylos documented whitelist entry {symbol!r} must have a non-empty reason"
        )


def test_ci_installs_makeutil_for_every_full_suite() -> None:
    """Every isolated full pytest suite must provision the pinned parser."""
    prerequisites = _text_sequence(
        _sole_recipe_rule("test").get("prerequisites"), subject="test prerequisites"
    )
    assert "makeutil" in prerequisites, (
        "Make test contract must require Makeutil before running contract tests"
    )
    lint_test = _workflow_job(".github/workflows/ci.yml", "lint-test")
    _assert_makeutil_environment(lint_test, contract="CI lint-test Makeutil contract")
    lint_parser = _sole_workflow_step("lint-test", "Install Makefile parser")
    _assert_makeutil_installation(
        lint_parser.get("run"), contract="CI lint-test Makeutil-install contract"
    )
    lint_step = _sole_workflow_step("lint-test", "Run lint and dead-code detection")
    assert lint_step.get("run") == "make lint", (
        "CI lint step must invoke the shared Makefile lint target"
    )
    coverage = _workflow_job(".github/workflows/coverage-main.yml", "coverage-upload")
    _assert_makeutil_environment(coverage, contract="main coverage Makeutil contract")
    coverage_parser = _sole_workflow_step(
        "coverage-upload",
        "Install Makefile parser",
        workflow_path=".github/workflows/coverage-main.yml",
    )
    _assert_makeutil_installation(
        coverage_parser.get("run"), contract="main coverage Makeutil-install contract"
    )
