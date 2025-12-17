"""Behavioural tests for concordat estate commands."""

from __future__ import annotations

import io
import typing as typ
from contextlib import redirect_stdout
from pathlib import Path

import pygit2
import pytest
import pytest_bdd.parsers as parsers
import requests
from betamax import Betamax
from pytest_bdd import given, scenarios, then, when
from ruamel.yaml import YAML

from concordat import cli, estate
from concordat.errors import ConcordatError
from concordat.estate import EstateRecord, RemoteProbe, list_estates, register_estate

from .conftest import RunResult

CASSETTE_DIR = Path(__file__).resolve().parent / "cassettes"
_yaml = YAML(typ="safe")

scenarios("features/estate.feature")


@pytest.fixture
def betamax_recorder() -> typ.Iterator[typ.Callable[[str, requests.Session], None]]:
    """Provide a helper for starting betamax sessions."""
    contexts: list[typ.Any] = []

    def start(name: str, session: requests.Session) -> None:
        recorder = Betamax(session, cassette_library_dir=str(CASSETTE_DIR))
        ctx = recorder.use_cassette(name)
        ctx.__enter__()
        contexts.append(ctx)

    yield start

    while contexts:
        ctx = contexts.pop()
        ctx.__exit__(None, None, None)


def _run_cli(arguments: list[str]) -> RunResult:
    buffer = io.StringIO()
    try:
        with redirect_stdout(buffer):
            result = cli.app(
                arguments,
                exit_on_error=False,
                print_error=False,
            )
    except ConcordatError as error:
        return RunResult(stdout=buffer.getvalue(), stderr=str(error), returncode=1)
    except SystemExit as exc:
        return RunResult(
            stdout=buffer.getvalue(), stderr="", returncode=int(exc.code or 0)
        )
    else:
        return RunResult(
            stdout=buffer.getvalue(), stderr="", returncode=int(result or 0)
        )


@given("an empty concordat config directory", target_fixture="config_dir")
def given_empty_config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create an isolated config directory for estate operations."""
    config_home = tmp_path / "xdg"
    config_home.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    return config_home


@given("sample estates are configured")
def given_sample_estates(config_dir: Path) -> None:
    """Seed the config file with two sample estates."""
    config_path = config_dir / "concordat" / "config.yaml"
    register_estate(
        EstateRecord(
            alias="core",
            repo_url="git@github.com:example/core.git",
            github_owner="example",
        ),
        config_path=config_path,
        set_active_if_missing=True,
    )
    register_estate(
        EstateRecord(
            alias="sandbox",
            repo_url="git@github.com:example/sandbox.git",
            github_owner="example",
        ),
        config_path=config_path,
        set_active_if_missing=False,
    )


@given(parsers.cfparse('betamax cassette "{name}" is active'))
def given_betamax_cassette(
    name: str,
    betamax_recorder: typ.Callable[[str, requests.Session], None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replay GitHub calls through betamax fixtures."""
    session = requests.Session()
    betamax_recorder(name, session)

    class FakeOrg:
        def __init__(self, owner: str) -> None:
            self.owner = owner

        def create_repository(
            self,
            name: str,
            *,
            description: str = "",
            homepage: str = "",
            private: bool = True,
            has_issues: bool = True,
            has_wiki: bool = True,
            license_template: str = "",
            auto_init: bool = False,
            gitignore_template: str = "",
            has_projects: bool = True,
        ) -> object:
            payload = {
                "name": name,
                "description": description,
                "homepage": homepage,
                "private": private,
                "has_issues": has_issues,
                "has_wiki": has_wiki,
                "license_template": license_template,
                "auto_init": auto_init,
                "gitignore_template": gitignore_template,
                "has_projects": has_projects,
            }
            session.post(
                f"https://api.github.com/orgs/{self.owner}/repos",
                json=payload,
                headers={"Authorization": "token betamax-token"},
            )
            return object()

    class FakeGithubClient:
        def repository(self, owner: str, name: str) -> object | None:
            resp = session.get(
                f"https://api.github.com/repos/{owner}/{name}",
                headers={"Authorization": "token betamax-token"},
            )
            return None if resp.status_code == 404 else object()

        def organization(self, owner: str) -> FakeOrg:
            session.get(
                f"https://api.github.com/orgs/{owner}",
                headers={"Authorization": "token betamax-token"},
            )
            return FakeOrg(owner)

        def me(self) -> object:
            return type("User", (), {"login": "example"})()

    fake_client = FakeGithubClient()
    monkeypatch.setattr(
        estate,
        "_build_client",
        lambda token, client_factory=None: fake_client,
    )


@given("the estate remote probe reports a missing repository")
def given_missing_remote(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend the remote repository does not exist."""
    monkeypatch.setattr(
        estate,
        "_probe_remote",
        lambda url: RemoteProbe(reachable=False, exists=False, empty=True, error=None),
    )
    monkeypatch.setattr(estate, "_bootstrap_template", lambda *args, **kwargs: None)


@given("the estate remote probe reports an empty existing repository")
def given_empty_existing_remote(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend the remote exists, is empty, and can be reached."""
    monkeypatch.setattr(
        estate,
        "_probe_remote",
        lambda url: RemoteProbe(reachable=True, exists=True, empty=True, error=None),
    )
    monkeypatch.setattr(estate, "_bootstrap_template", lambda *args, **kwargs: None)


@given(
    parsers.cfparse('the operator responds "{response}" to prompts'),
    target_fixture="prompt_log",
)
def given_prompt_responder(
    response: str,
    monkeypatch: pytest.MonkeyPatch,
) -> list[str]:
    """Capture input prompts and return the configured response."""
    prompts: list[str] = []

    def fake_input(message: str = "") -> str:
        prompts.append(message)
        return response

    monkeypatch.setattr("builtins.input", fake_input)
    return prompts


@given("a local estate remote", target_fixture="local_remote_path")
def given_local_estate_remote(tmp_path: Path) -> Path:
    """Expose a bare local Git repository for estate testing."""
    remote = tmp_path / "estate-remote.git"
    pygit2.init_repository(str(remote), bare=True)
    return remote


@when("I run concordat estate ls")
def when_run_estate_ls(cli_invocation: dict[str, RunResult]) -> None:
    """Execute the estate listing command."""
    cli_invocation["result"] = _run_cli(["estate", "ls"])


@when(
    "I run concordat estate init core "
    "git@github.com:example/platform-estate.git with confirmation"
)
def when_run_estate_init(cli_invocation: dict[str, RunResult]) -> None:
    """Initialise an estate remote via CLI."""
    cli_invocation["result"] = _run_cli(
        [
            "estate",
            "init",
            "core",
            "git@github.com:example/platform-estate.git",
            "--github-token",
            "betamax-token",
            "--yes",
        ]
    )


@when(
    "I run concordat estate init core "
    "git@github.com:example/platform-estate.git interactively"
)
def when_run_estate_init_interactively(cli_invocation: dict[str, RunResult]) -> None:
    """Initialise an estate remote without `--yes` to exercise prompts."""
    cli_invocation["result"] = _run_cli(
        [
            "estate",
            "init",
            "core",
            "git@github.com:example/platform-estate.git",
        ]
    )


@when(
    "I run concordat estate init core "
    "git@github.com:example/platform-estate.git with token"
)
def when_run_estate_init_with_token(cli_invocation: dict[str, RunResult]) -> None:
    """Initialise an estate remote with a token but without `--yes`."""
    cli_invocation["result"] = _run_cli(
        [
            "estate",
            "init",
            "core",
            "git@github.com:example/platform-estate.git",
            "--github-token",
            "betamax-token",
        ]
    )


@when(
    parsers.cfparse(
        'I run concordat estate init {alias} using that remote for owner "{owner}"'
    )
)
def when_run_estate_init_local(
    alias: str,
    owner: str,
    local_remote_path: Path,
    cli_invocation: dict[str, RunResult],
) -> None:
    """Initialise an estate using the local remote."""
    cli_invocation["result"] = _run_cli(
        [
            "estate",
            "init",
            alias,
            str(local_remote_path),
            "--github-owner",
            owner,
            "--yes",
        ]
    )


@then("the CLI prints")
def then_cli_prints(
    cli_invocation: dict[str, RunResult],
    docstring: str,
) -> None:
    """Assert the CLI output matches the expected text."""
    expected = [line.strip() for line in docstring.strip().splitlines() if line.strip()]
    result = cli_invocation["result"]
    actual = [
        line.strip() for line in result.stdout.strip().splitlines() if line.strip()
    ]
    assert actual == expected


@then("the command succeeds")
def then_command_succeeds(cli_invocation: dict[str, RunResult]) -> None:
    """Ensure the previous CLI call returned success."""
    result = cli_invocation["result"]
    assert result.returncode == 0, result.stderr or result.stdout


@then(parsers.cfparse('the command fails with "{message}"'))
def then_command_fails_with(
    cli_invocation: dict[str, RunResult],
    message: str,
) -> None:
    """Ensure the previous CLI call returned a failure with the expected message."""
    result = cli_invocation["result"]
    assert result.returncode != 0
    assert message in result.stderr


@then(parsers.cfparse('estate "{alias}" is recorded in the config'))
def then_estate_recorded(alias: str) -> None:
    """Confirm that the provided estate alias exists in config."""
    aliases = [record.alias for record in list_estates()]
    assert alias in aliases


@then(parsers.cfparse('estate "{alias}" is not recorded in the config'))
def then_estate_not_recorded(alias: str) -> None:
    """Confirm that the provided estate alias does not exist in config."""
    aliases = [record.alias for record in list_estates()]
    assert alias not in aliases


@then("the CLI prompts")
def then_cli_prompts(prompt_log: list[str], docstring: str) -> None:
    """Assert the captured prompts match the expected docstring."""
    expected = [
        line.rstrip() for line in docstring.strip().splitlines() if line.strip()
    ]
    actual = [prompt.rstrip() for prompt in prompt_log]
    assert actual == expected


@then("the estate inventory contains no sample repositories")
def then_inventory_sanitised(local_remote_path: Path, tmp_path: Path) -> None:
    """Ensure the bootstrapped inventory file is empty."""
    clone_path = tmp_path / "estate-clone"
    pygit2.clone_repository(str(local_remote_path), str(clone_path))
    inventory_path = clone_path / "tofu" / "inventory" / "repositories.yaml"
    data = _yaml.load(inventory_path.read_text(encoding="utf-8")) or {}
    assert data.get("repositories") == []
