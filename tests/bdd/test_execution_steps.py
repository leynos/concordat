"""Behavioural tests for concordat plan/apply commands."""

from __future__ import annotations

import io
import os
import shlex
import sys
import textwrap
import typing as typ
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pygit2
import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from concordat import cli
from concordat.errors import ConcordatError
from concordat.estate import EstateRecord, register_estate

from .conftest import RunResult

scenarios("features/execution.feature")

ERROR_WORKSPACE_MISSING = "Workspace path not reported."


@pytest.fixture
def execution_state() -> dict[str, typ.Any]:
    """State shared between steps in execution scenarios."""
    return {}


@given("an isolated concordat config directory", target_fixture="config_dir")
def given_isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point concordat at a temporary config home."""
    config_home = tmp_path / "config"
    config_home.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    return config_home


@given("an isolated concordat cache directory")
def given_isolated_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point concordat at a temporary cache home."""
    cache_home = tmp_path / "cache"
    cache_home.mkdir()
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache_home))


@given("a fake estate repository is registered")
def given_fake_estate(
    tmp_path: Path,
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Seed the config with a local estate repository."""
    repo_path = tmp_path / "estate-remote"
    repo_path.mkdir()
    repository = pygit2.init_repository(str(repo_path), initial_head="main")
    (repo_path / "README.md").write_text("seed\n", encoding="utf-8")
    index = repository.index
    index.add("README.md")
    index.write()
    tree_oid = index.write_tree()
    sig = pygit2.Signature("Test", "test@example.com")
    repository.create_commit(
        "refs/heads/main",
        sig,
        sig,
        "seed",
        tree_oid,
        [],
    )
    config_path = config_dir / "concordat" / "config.yaml"
    register_estate(
        EstateRecord(alias="core", repo_url=str(repo_path), github_owner="example"),
        config_path=config_path,
        set_active_if_missing=True,
    )
    monkeypatch.setenv("GITHUB_TOKEN", "placeholder-token")


@given("GITHUB_TOKEN is unset")
def given_token_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear any configured GitHub token."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)


@given(parsers.cfparse('GITHUB_TOKEN is set to "{token}"'))
def given_token_set(token: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Force concordat to use the provided GitHub token."""
    monkeypatch.setenv("GITHUB_TOKEN", token)


@given("a fake tofu binary logs invocations")
def given_fake_tofu(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    execution_state: dict[str, typ.Any],
) -> None:
    """Provide a stub tofu binary for the CLI to invoke."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "tofu"
    log_path = tmp_path / "fake-tofu.log"
    script.write_text(
        "#!"
        + sys.executable
        + "\n"
        + textwrap.dedent(
            """
            import json
            import os
            import sys

            LOG = os.environ.get("FAKE_TOFU_LOG")
            ARGS = sys.argv[1:]
            if LOG:
                with open(LOG, "a", encoding="utf-8") as handle:
                    handle.write(" ".join(ARGS) + "\\n")
            if len(ARGS) >= 2 and ARGS[0] == "version" and ARGS[1] == "-json":
                payload = {
                    "terraform_version": "1.6.0",
                    "platform": "linux_amd64",
                }
                print(json.dumps(payload))
                raise SystemExit(0)
            command = ARGS[0] if ARGS else ""
            if command == "plan":
                code = int(os.environ.get("FAKE_TOFU_PLAN_EXIT_CODE", "0"))
            elif command == "apply":
                code = int(os.environ.get("FAKE_TOFU_APPLY_EXIT_CODE", "0"))
            else:
                code = 0
            print("fake-tofu", " ".join(ARGS))
            raise SystemExit(code)
            """
        ),
        encoding="utf-8",
    )
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ.get('PATH', '')}")
    monkeypatch.setenv("FAKE_TOFU_LOG", str(log_path))
    execution_state["tofu_log"] = log_path


def _run_cli(arguments: list[str]) -> RunResult:
    buffer_out = io.StringIO()
    buffer_err = io.StringIO()
    try:
        with redirect_stdout(buffer_out), redirect_stderr(buffer_err):
            result = cli.app(
                arguments,
                exit_on_error=False,
                print_error=False,
            )
    except ConcordatError as error:
        buffer_err.write(str(error))
        return RunResult(
            stdout=buffer_out.getvalue(),
            stderr=buffer_err.getvalue(),
            returncode=1,
        )
    except SystemExit as exc:
        return RunResult(
            stdout=buffer_out.getvalue(),
            stderr=buffer_err.getvalue(),
            returncode=int(exc.code or 0),
        )
    else:
        return RunResult(
            stdout=buffer_out.getvalue(),
            stderr=buffer_err.getvalue(),
            returncode=int(result or 0),
        )


@when(parsers.cfparse("I run concordat {command:w}"))
def when_run_command(
    command: str,
    cli_invocation: dict[str, RunResult],
) -> None:
    """Run the CLI command without additional options."""
    cli_invocation["result"] = _run_cli([command])


@when(parsers.cfparse('I run concordat {command:w} with options "{options}"'))
def when_run_command_with_options(
    command: str,
    options: str,
    cli_invocation: dict[str, RunResult],
) -> None:
    """Run the CLI command with space-separated options."""
    args = [command]
    if options.strip():
        args.extend(shlex.split(options))
    cli_invocation["result"] = _run_cli(args)


def _workspace_path(result: RunResult) -> Path:
    for line in result.stderr.splitlines():
        if line.startswith("execution workspace:"):
            return Path(line.split(":", 1)[1].strip())
    raise AssertionError(ERROR_WORKSPACE_MISSING)


@then(parsers.parse("the command exits with code {code:d}"))
def then_command_exit(cli_invocation: dict[str, RunResult], code: int) -> None:
    """Assert the CLI exited with the expected status."""
    assert cli_invocation["result"].returncode == code


@then(parsers.parse('the command fails with message "{message}"'))
def then_command_fails(
    cli_invocation: dict[str, RunResult],
    message: str,
) -> None:
    """Assert the CLI failed and reported a specific message."""
    result = cli_invocation["result"]
    assert result.returncode != 0
    assert message in result.stderr


@then("the execution workspace has been removed")
def then_workspace_removed(cli_invocation: dict[str, RunResult]) -> None:
    """Assert that plan/apply cleaned up its temporary directory."""
    workspace = _workspace_path(cli_invocation["result"])
    assert not workspace.exists()


@then("the execution workspace remains on disk")
def then_workspace_retained(cli_invocation: dict[str, RunResult]) -> None:
    """Assert that --keep-workdir preserves the workspace."""
    workspace = _workspace_path(cli_invocation["result"])
    assert workspace.exists()


@then(parsers.cfparse('fake tofu commands were "{commands}"'))
def then_fake_tofu_commands(
    cli_invocation: dict[str, RunResult],
    execution_state: dict[str, typ.Any],
    commands: str,
) -> None:
    """Validate that the fake tofu script observed the expected calls."""
    log_path = execution_state.get("tofu_log")
    assert log_path, "fake tofu log path missing"
    expected = [item.strip() for item in commands.split("|") if item.strip()]
    with Path(log_path).open("r", encoding="utf-8") as handle:
        actual = [line for raw_line in handle if (line := raw_line.strip())]
    assert actual == expected
