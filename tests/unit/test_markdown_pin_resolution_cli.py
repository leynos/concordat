"""End-to-end tests for PD-006 pin resolution through the rule-run command.

Each test lays out a generator scenario, points `concordat artefact rule run`
at a local GitHub API double with `--github-api-url`, and reads the rendered
JSON. The production path runs unpatched: the command composes the GitHub
resolver, the package builder builds the envelope with the real `makeutil`,
the pins resolve over HTTP, and the real Conftest judges the policy.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
import typing as typ

import pytest

from concordat import cli, credentials
from concordat.credentials import InsecureCredentialsError
from tests.helpers.github_api import Reply, git_object_routes

if typ.TYPE_CHECKING:
    import types

    import pytest_mock

    from tests.helpers.github_api import FakeGithubApi

RULE_ID: typ.Final = "markdown-formatting-baseline"
ACTION: typ.Final = "DavidAnson/markdownlint-cli2-action"
GENERATOR: typ.Final = (
    pathlib.Path(__file__).resolve().parents[2]
    / "platform-standards/canon/lint-rules"
    / RULE_ID
    / "fixtures/generate.py"
)


def _generator() -> types.ModuleType:
    """Import the package's fixture generator by path."""
    spec = importlib.util.spec_from_file_location(
        "markdown_pin_cli_generate", GENERATOR
    )
    if spec is None or spec.loader is None:
        message = "could not load the fixture generator"
        raise ImportError(message)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


GENERATE: typ.Final = _generator()
COMMIT: typ.Final[str] = GENERATE.V24_2_0_COMMIT
TAG_OBJECT: typ.Final[str] = GENERATE.V24_2_0_TAG_OBJECT
UNKNOWN: typ.Final = "0123456789abcdef0123456789abcdef01234567"


def _commit_path(sha: str) -> str:
    return f"/repos/{ACTION}/git/commits/{sha}"


def _tag_path(sha: str) -> str:
    return f"/repos/{ACTION}/git/tags/{sha}"


@pytest.fixture
def github(fake_github_api: FakeGithubApi) -> FakeGithubApi:
    """Answer as GitHub does for v24.2.0: a commit and its tag object."""
    fake_github_api.routes.update(
        git_object_routes(ACTION, commits=[COMMIT], tags={TAG_OBJECT: COMMIT})
    )
    return fake_github_api


def _run(
    scenario: str,
    tmp_path: pathlib.Path,
    github: FakeGithubApi,
    capsys: pytest.CaptureFixture[str],
) -> tuple[int, dict[str, typ.Any]]:
    """Lay *scenario* out, run the rule over it, and return the exit and JSON."""
    checkout = tmp_path / scenario
    checkout.mkdir()
    GENERATE.lay_out(GENERATE.SCENARIOS[scenario], checkout)
    returncode = _main([
        "artefact",
        "rule",
        "run",
        RULE_ID,
        "--repo",
        str(checkout),
        "--format",
        "json",
        "--github-api-url",
        github.url,
    ])
    return returncode, json.loads(capsys.readouterr().out)


def _main(argv: list[str]) -> int:
    """Run the CLI, returning its exit status however cyclopts reports it."""
    try:
        return cli.main(argv)
    except SystemExit as exit_:
        return int(exit_.code or 0)


def _pd006(document: dict[str, typ.Any]) -> list[tuple[str, str]]:
    """Return the verdict and message of every PD-006 finding."""
    return [
        (finding["verdict"], finding["message"])
        for finding in document["findings"]
        if finding["rule_id"] == "PD-006"
    ]


@pytest.mark.timeout(120)
def test_a_commit_pin_is_compliant_after_one_lookup(
    tmp_path: pathlib.Path, github: FakeGithubApi, capsys: pytest.CaptureFixture[str]
) -> None:
    """A pin the commit endpoint knows passes, and the tag endpoint is not asked."""
    returncode, document = _run("compliant", tmp_path, github, capsys)
    assert (returncode, document["verdict"]) == (0, "compliant"), document
    assert github.requested == [_commit_path(COMMIT)]


@pytest.mark.timeout(120)
def test_a_tag_object_pin_is_refused_naming_the_peeled_commit(
    tmp_path: pathlib.Path, github: FakeGithubApi, capsys: pytest.CaptureFixture[str]
) -> None:
    """A pin only the tag endpoint knows fails, and the finding names the commit."""
    returncode, document = _run("workflow_tag_object", tmp_path, github, capsys)
    assert returncode == 1, document
    [(verdict, message)] = _pd006(document)
    assert verdict == "noncompliant", message
    assert message.endswith(f"not a commit; pin the commit it peels to, {COMMIT}"), (
        message
    )
    assert github.requested == [_commit_path(TAG_OBJECT), _tag_path(TAG_OBJECT)]


@pytest.mark.timeout(120)
def test_an_unknown_pin_is_indeterminate(
    tmp_path: pathlib.Path, github: FakeGithubApi, capsys: pytest.CaptureFixture[str]
) -> None:
    """A SHA neither endpoint knows is indeterminate, never a pass."""
    returncode, document = _run("workflow_pin_unresolved", tmp_path, github, capsys)
    assert returncode == 1, document
    [(verdict, message)] = _pd006(document)
    assert verdict == "indeterminate", message
    assert f"{UNKNOWN} is neither a commit nor a tag" in message, message


@pytest.mark.timeout(120)
def test_a_refused_lookup_is_indeterminate_without_asking_for_a_tag(
    tmp_path: pathlib.Path, github: FakeGithubApi, capsys: pytest.CaptureFixture[str]
) -> None:
    """A refusal from the commit endpoint is not taken as "not a commit"."""
    github.routes[_commit_path(COMMIT)] = Reply(403, {"message": "rate limited"})
    returncode, document = _run("compliant", tmp_path, github, capsys)
    assert returncode == 1, document
    [(verdict, message)] = _pd006(document)
    assert verdict == "indeterminate", message
    assert f"git/commits/{COMMIT} could not be read" in message, message
    assert github.requested == [_commit_path(COMMIT)]


@pytest.mark.timeout(120)
def test_a_checkout_without_pins_asks_nothing_and_reads_no_credentials(
    tmp_path: pathlib.Path,
    github: FakeGithubApi,
    capsys: pytest.CaptureFixture[str],
    mocker: pytest_mock.MockFixture,
) -> None:
    """With no full-SHA pin, the client is never built and GitHub never asked."""
    token = mocker.patch.object(credentials, "github_token")
    returncode, document = _run("workflow_floating_tag", tmp_path, github, capsys)
    assert returncode == 1, document
    assert github.requested == []
    token.assert_not_called()


@pytest.mark.timeout(120)
def test_unreadable_credentials_are_an_operational_failure(
    tmp_path: pathlib.Path,
    github: FakeGithubApi,
    capsys: pytest.CaptureFixture[str],
    mocker: pytest_mock.MockFixture,
) -> None:
    """A credentials file the command cannot use stops the audit with exit 2."""
    mocker.patch.object(
        credentials,
        "github_token",
        side_effect=InsecureCredentialsError(tmp_path / "credentials.yaml"),
    )
    checkout = tmp_path / "compliant"
    checkout.mkdir()
    GENERATE.lay_out(GENERATE.SCENARIOS["compliant"], checkout)
    returncode = _main([
        "artefact",
        "rule",
        "run",
        RULE_ID,
        "--repo",
        str(checkout),
        "--github-api-url",
        github.url,
    ])
    assert returncode == 2
    assert "cannot read the GitHub credentials" in capsys.readouterr().err
    assert github.requested == []
