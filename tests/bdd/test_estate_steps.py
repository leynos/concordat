"""Behavioural tests for concordat estate commands."""

from __future__ import annotations

import io
import typing as typ
from contextlib import redirect_stdout
from pathlib import Path

import pytest
import pytest_bdd.parsers as parsers
import requests
from betamax import Betamax
from pytest_bdd import given, scenarios, then, when

from concordat import cli, estate
from concordat.errors import ConcordatError
from concordat.estate import EstateRecord, RemoteProbe, list_estates, register_estate

from .conftest import RunResult

CASSETTE_DIR = Path(__file__).resolve().parent / "cassettes"

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
    config_home = tmp_path / "xdg"
    config_home.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    return config_home


@given("sample estates are configured")
def given_sample_estates(config_dir: Path) -> None:
    config_path = config_dir / "concordat" / "config.yaml"
    register_estate(
        EstateRecord(alias="core", repo_url="git@github.com:example/core.git"),
        config_path=config_path,
        set_active_if_missing=True,
    )
    register_estate(
        EstateRecord(alias="sandbox", repo_url="git@github.com:example/sandbox.git"),
        config_path=config_path,
        set_active_if_missing=False,
    )


@given(parsers.cfparse('betamax cassette "{name}" is active'))
def given_betamax_cassette(
    name: str,
    betamax_recorder,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = requests.Session()
    betamax_recorder(name, session)

    class FakeOrg:
        def __init__(self, owner: str) -> None:
            self.owner = owner

        def create_repository(
            self,
            name: str,
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
    monkeypatch.setattr(
        estate,
        "_probe_remote",
        lambda url: RemoteProbe(reachable=False, exists=False, empty=True, error=None),
    )
    monkeypatch.setattr(estate, "_bootstrap_template", lambda *args, **kwargs: None)


@when("I run concordat estate ls")
def when_run_estate_ls(cli_invocation: dict[str, RunResult]) -> None:
    cli_invocation["result"] = _run_cli(["estate", "ls"])


@when(
    "I run concordat estate init core "
    "git@github.com:example/platform-estate.git with confirmation"
)
def when_run_estate_init(cli_invocation: dict[str, RunResult]) -> None:
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


@then("the CLI prints")
def then_cli_prints(
    cli_invocation: dict[str, RunResult],
    docstring: str,
) -> None:
    expected = [line.strip() for line in docstring.strip().splitlines() if line.strip()]
    result = cli_invocation["result"]
    actual = [
        line.strip() for line in result.stdout.strip().splitlines() if line.strip()
    ]
    assert actual == expected


@then("the command succeeds")
def then_command_succeeds(cli_invocation: dict[str, RunResult]) -> None:
    result = cli_invocation["result"]
    assert result.returncode == 0, result.stderr or result.stdout


@then(parsers.cfparse('estate "{alias}" is recorded in the config'))
def then_estate_recorded(alias: str) -> None:
    aliases = [record.alias for record in list_estates()]
    assert alias in aliases
