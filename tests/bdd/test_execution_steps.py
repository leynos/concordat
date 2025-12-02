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
from tests.helpers.persistence import seed_persistence_files

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
    execution_state: dict[str, typ.Any],
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
    execution_state["estate_repo_path"] = repo_path


@given("the estate repository has remote state configured")
def given_estate_persistence(execution_state: dict[str, typ.Any]) -> None:
    """Add persistence manifest and backend config to the estate repo."""
    repo_path = execution_state.get("estate_repo_path")
    assert repo_path, "estate repo path missing"
    repository = pygit2.Repository(str(repo_path))

    seed_persistence_files(Path(repo_path))

    index = repository.index
    index.add_all()
    index.write()
    tree_oid = index.write_tree()
    sig = pygit2.Signature("Test", "test@example.com")
    repository.create_commit(
        "refs/heads/main",
        sig,
        sig,
        "add persistence backend",
        tree_oid,
        [repository.head.target],
    )


@given("GITHUB_TOKEN is unset")
def given_token_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear any configured GitHub token."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)


@given(parsers.cfparse('GITHUB_TOKEN is set to "{token}"'))
def given_token_set(token: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Force concordat to use the provided GitHub token."""
    monkeypatch.setenv("GITHUB_TOKEN", token)


@given("remote backend credentials are set")
def given_remote_backend_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provide S3-compatible credentials via Scaleway aliases."""
    monkeypatch.setenv("SCW_ACCESS_KEY", "scw-access-key")
    monkeypatch.setenv("SCW_SECRET_KEY", "scw-secret-key")
    for variable in (
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "SPACES_ACCESS_KEY_ID",
        "SPACES_SECRET_ACCESS_KEY",
    ):
        monkeypatch.delenv(variable, raising=False)


@given("remote backend credentials are set via SPACES")
def given_remote_backend_credentials_spaces(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provide S3-compatible credentials via DigitalOcean Spaces aliases."""
    monkeypatch.setenv("SPACES_ACCESS_KEY_ID", "spaces-access-key-id")
    monkeypatch.setenv("SPACES_SECRET_ACCESS_KEY", "spaces-secret-access-key")
    for variable in (
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "SCW_ACCESS_KEY",
        "SCW_SECRET_KEY",
    ):
        monkeypatch.delenv(variable, raising=False)


@given("remote backend credentials are set via AWS")
def given_remote_backend_credentials_aws(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provide native AWS credentials."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "aws-access-key-id")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "aws-secret-access-key")
    for variable in (
        "SCW_ACCESS_KEY",
        "SCW_SECRET_KEY",
        "SPACES_ACCESS_KEY_ID",
        "SPACES_SECRET_ACCESS_KEY",
    ):
        monkeypatch.delenv(variable, raising=False)


@given("remote backend credentials are missing")
def given_remote_backend_credentials_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure no backend credentials are present in the environment."""
    for variable in (
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "SCW_ACCESS_KEY",
        "SCW_SECRET_KEY",
        "SPACES_ACCESS_KEY_ID",
        "SPACES_SECRET_ACCESS_KEY",
    ):
        monkeypatch.delenv(variable, raising=False)


@given("aws-style backend secrets are present in the environment")
def given_aws_style_backend_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    """Seed representative AWS-like credentials for leak checks."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAFAKEACCESSKEYID1234")
    monkeypatch.setenv(
        "AWS_SECRET_ACCESS_KEY",
        "wJalrXUtnFEMI/K7MDENG/bPxRfiCYFAKESECRET",
    )


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
    monkeypatch.setenv(
        "PATH",
        f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
    )
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


@then(parsers.cfparse('the backend details mention bucket "{bucket}" and key "{key}"'))
def then_backend_details_logged(
    cli_invocation: dict[str, RunResult],
    bucket: str,
    key: str,
) -> None:
    """Ensure stderr includes backend metadata but not secrets."""
    stderr = cli_invocation["result"].stderr
    assert bucket in stderr
    assert key in stderr


@then("no backend secrets are logged")
def then_no_backend_secrets(cli_invocation: dict[str, RunResult]) -> None:
    """Assert that secret-like values are absent from output."""
    result = cli_invocation["result"]
    secrets_to_check = ["scw-secret-key", "SCW_SECRET_KEY"]
    for env_var in (
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "SPACES_ACCESS_KEY_ID",
        "SPACES_SECRET_ACCESS_KEY",
    ):
        value = os.environ.get(env_var)
        if value:
            secrets_to_check.append(value)

    for secret in secrets_to_check:
        assert secret not in result.stderr
        assert secret not in result.stdout
