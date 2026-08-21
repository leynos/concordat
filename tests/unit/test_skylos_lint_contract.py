"""Contract tests for Skylos dead-code detection in Make and CI.

The scan accepts ``--config-file`` before a path, whereas the standalone
``whitelist`` subcommand must appear immediately after ``skylos``. Makeutil
parses the Makefile into structured rules and variables, so these tests assert
that order without depending on whitespace or nearby source text.
"""

from __future__ import annotations

import functools
import json
import shlex
import subprocess
import tomllib
import typing as typ
from pathlib import Path

from ruamel.yaml import YAML

REPOSITORY_ROOT = Path(__file__).parents[2]
_MAKEUTIL_COMMAND: typ.Final = ("makeutil", "parse", "Makefile")


@functools.cache
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


def _sole_variable(name: str) -> dict[str, object]:
    """Return Makeutil's sole variable fact for ``name``."""
    variables = _objects(_makefile_report().get("variables"), subject="variables")
    matches = [variable for variable in variables if variable.get("name") == name]
    assert len(matches) == 1, (
        f"expected one Makefile variable named {name!r}, found {len(matches)}"
    )
    return matches[0]


def _sole_recipe_rule(target: str) -> dict[str, object]:
    """Return the only parsed rule for ``target`` that has recipes."""
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


def _sole_ci_step(name: str) -> dict[str, object]:
    """Return the sole named step from the CI lint-and-test job."""
    yaml = YAML(typ="safe")
    workflow = _mapping(
        yaml.load((REPOSITORY_ROOT / ".github/workflows/ci.yml").read_text()),
        subject="CI workflow",
    )
    jobs = _mapping(workflow.get("jobs"), subject="CI workflow jobs")
    lint_test = _mapping(jobs.get("lint-test"), subject="CI lint-test job")
    steps = _objects(lint_test.get("steps"), subject="CI lint-test steps")
    matches = [step for step in steps if step.get("name") == name]
    assert len(matches) == 1, f"expected one CI step named {name!r}"
    return matches[0]


def test_lint_recipe_runs_the_production_dead_code_gate() -> None:
    """``make lint`` must scan production code with Skylos's strict gate."""
    skylos_commands = [
        command for command in _recipe_tokens("lint") if command[:1] == ("$(SKYLOS)",)
    ]

    assert skylos_commands == [
        (
            "$(SKYLOS)",
            "$(SKYLOS_PRODUCTION_TARGETS)",
            "--category",
            "dead_code",
            "--gate",
            "--format",
            "concise",
            "--no-upload",
            "--no-provenance",
            "--no-grep-verify",
        )
    ]


def test_whitelist_target_uses_skylos_subcommand_contract() -> None:
    """``skylos whitelist`` must precede the name and have no scan options."""
    assert _variable_tokens("SKYLOS") == (
        "$(SKYLOS_COMMAND)",
        "--config-file",
        "pyproject.toml",
    )
    assert _variable_tokens("SKYLOS_WHITELIST") == (
        "$(SKYLOS_COMMAND)",
        "whitelist",
    )

    whitelist_commands = [
        command
        for command in _recipe_tokens("skylos-allow")
        if command[:1] == ("$(SKYLOS_WHITELIST)",)
    ]
    assert whitelist_commands == [("$(SKYLOS_WHITELIST)", "$${SKYLOS_NAME}")]


def test_skylos_configuration_is_strict() -> None:
    """The reviewed configuration must keep the dead-code gate strict."""
    with (REPOSITORY_ROOT / "pyproject.toml").open("rb") as configuration_file:
        configuration = tomllib.load(configuration_file)

    tool = _mapping(configuration.get("tool"), subject="tool configuration")
    skylos = _mapping(tool.get("skylos"), subject="Skylos configuration")
    gate = _mapping(skylos.get("gate"), subject="Skylos gate configuration")
    assert gate.get("strict") is True


def test_ci_installs_the_makefile_parser_before_the_lint_target() -> None:
    """CI must install Makeutil before it runs the shared lint target."""
    parser_step = _sole_ci_step("Install Makefile parser")
    parser_run = parser_step.get("run")
    assert isinstance(parser_run, str)
    assert "rustup toolchain install" in parser_run
    assert 'cargo +"${MAKEUTIL_TOOLCHAIN}" install' in parser_run
    assert '--rev "${MAKEUTIL_REVISION}"' in parser_run
    assert "makeutil" in parser_run

    lint_step = _sole_ci_step("Run lint and dead-code detection")
    assert lint_step.get("run") == "make lint"
