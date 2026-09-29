"""End-to-end tests for PD-006 pin resolution through the rule-run command.

Each test lays out a generator scenario, points `concordat artefact rule run`
at a local GitHub API double with `--github-api-url`, and reads the rendered
JSON. The production path runs unpatched: the command composes the GitHub
resolver, the package builder builds the envelope with the real `makeutil`,
the pins resolve over HTTP, and the real Conftest judges the policy.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import pathlib
import subprocess
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


@pytest.fixture(autouse=True)
def _no_gh_cli_token(mocker: pytest_mock.MockFixture) -> None:
    """Keep the host's `gh` login out of these runs; one test opts back in."""
    mocker.patch.object(cli, "_gh_cli_token", return_value=None)


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
    *extra: str,
) -> tuple[int, dict[str, typ.Any]]:
    """Lay *scenario* out, run the rule over it, and return the exit and JSON.

    *extra* arguments follow the command's own. Each call lays out a fresh
    checkout, so a test may run the rule twice from one `tmp_path`.

    Returns
    -------
    tuple[int, dict[str, typing.Any]]
        The exit status and the decoded JSON document.
    """
    checkout = tmp_path / f"{scenario}-{len(list(tmp_path.iterdir()))}"
    checkout.mkdir()
    GENERATE.lay_out(GENERATE.SCENARIOS[scenario], checkout)
    argv = [
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
        *extra,
    ]
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        returncode = _main(argv)
    return returncode, json.loads(output.getvalue())


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
    tmp_path: pathlib.Path, github: FakeGithubApi
) -> None:
    """A pin the commit endpoint knows passes, and the tag endpoint is not asked."""
    returncode, document = _run("compliant", tmp_path, github)
    assert (returncode, document["verdict"]) == (0, "compliant"), document
    assert github.requested == [_commit_path(COMMIT)]


@pytest.mark.timeout(120)
def test_a_tag_object_pin_is_refused_naming_the_peeled_commit(
    tmp_path: pathlib.Path, github: FakeGithubApi
) -> None:
    """A pin only the tag endpoint knows fails, and the finding names the commit."""
    returncode, document = _run("workflow_tag_object", tmp_path, github)
    assert returncode == 1, document
    [(verdict, message)] = _pd006(document)
    assert verdict == "noncompliant", message
    assert message.endswith(f"not a commit; pin the commit it peels to, {COMMIT}"), (
        message
    )
    assert github.requested == [_commit_path(TAG_OBJECT), _tag_path(TAG_OBJECT)]


@pytest.mark.timeout(120)
def test_an_unknown_pin_is_indeterminate(
    tmp_path: pathlib.Path, github: FakeGithubApi
) -> None:
    """A SHA neither endpoint knows is indeterminate, never a pass."""
    returncode, document = _run("workflow_pin_unresolved", tmp_path, github)
    assert returncode == 1, document
    [(verdict, message)] = _pd006(document)
    assert verdict == "indeterminate", message
    assert f"{UNKNOWN} is neither a commit nor a tag" in message, message


@pytest.mark.timeout(120)
def test_a_refused_lookup_is_indeterminate_without_asking_for_a_tag(
    tmp_path: pathlib.Path, github: FakeGithubApi
) -> None:
    """A refusal from the commit endpoint is not taken as "not a commit"."""
    github.routes[_commit_path(COMMIT)] = Reply(403, {"message": "rate limited"})
    returncode, document = _run("compliant", tmp_path, github)
    assert returncode == 1, document
    [(verdict, message)] = _pd006(document)
    assert verdict == "indeterminate", message
    assert f"git/commits/{COMMIT} could not be read" in message, message
    assert github.requested == [_commit_path(COMMIT)]


@pytest.mark.timeout(120)
def test_a_checkout_without_pins_asks_nothing_and_reads_no_credentials(
    tmp_path: pathlib.Path,
    github: FakeGithubApi,
    mocker: pytest_mock.MockFixture,
) -> None:
    """With no full-SHA pin, the client is never built and GitHub never asked."""
    token = mocker.patch.object(credentials, "github_token")
    returncode, document = _run("workflow_floating_tag", tmp_path, github)
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
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
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


@pytest.mark.timeout(120)
def test_the_gh_cli_token_authenticates_when_no_token_is_configured(
    tmp_path: pathlib.Path,
    github: FakeGithubApi,
    mocker: pytest_mock.MockFixture,
) -> None:
    """Without GITHUB_TOKEN, the lookups carry the token `gh auth token` gives."""
    token = mocker.patch.object(cli, "_gh_cli_token", return_value="gho-fixture")
    returncode, _ = _run("compliant", tmp_path, github)
    assert returncode == 0
    assert github.authorizations == ["Bearer gho-fixture"]
    token.assert_called_once_with("127.0.0.1")


@pytest.mark.timeout(120)
def test_a_rate_limited_lookup_is_reported_as_such(
    tmp_path: pathlib.Path, github: FakeGithubApi
) -> None:
    """A spent rate limit is indeterminate with its remedy, not an unknown pin."""
    github.routes[_commit_path(COMMIT)] = Reply(
        429, {"message": "API rate limit exceeded"}
    )
    returncode, document = _run("compliant", tmp_path, github)
    assert returncode == 1, document
    [(verdict, message)] = _pd006(document)
    assert verdict == "indeterminate", message
    assert (
        "the GitHub API rate limit is spent; wait for the limit to reset" in message
    ), message


@pytest.mark.parametrize(
    ("completed", "expected"),
    [
        pytest.param(
            subprocess.CompletedProcess([], 0, "gho-x\n", ""), "gho-x", id="logged-in"
        ),
        pytest.param(
            subprocess.CompletedProcess([], 1, "stale\n", "not logged in"),
            None,
            id="logged-out",
        ),
        pytest.param(subprocess.CompletedProcess([], 0, "\n", ""), None, id="empty"),
        pytest.param(FileNotFoundError("gh"), None, id="no-gh"),
        pytest.param(subprocess.TimeoutExpired(["gh"], 10), None, id="slow"),
    ],
)
def test_the_gh_cli_token_is_read_or_skipped(
    completed: subprocess.CompletedProcess[str] | Exception,
    expected: str | None,
    mocker: pytest_mock.MockFixture,
) -> None:
    """`gh auth token` supplies a token when it has one and never stops the run."""
    mocker.stopall()
    run = mocker.patch.object(subprocess, "run")
    if isinstance(completed, Exception):
        run.side_effect = completed
    else:
        run.return_value = completed
    assert cli._gh_cli_token("github.com") == expected
    assert run.call_args.args[0] == ["gh", "auth", "token", "--hostname", "github.com"]


@pytest.mark.parametrize(
    ("api_url", "host"),
    [
        pytest.param("https://api.github.com", "github.com", id="github.com"),
        pytest.param("https://api.github.com/", "github.com", id="trailing-slash"),
        pytest.param(
            "https://ghe.example.com/api/v3", "ghe.example.com", id="enterprise"
        ),
        pytest.param("http://127.0.0.1:8123", "127.0.0.1", id="loopback"),
    ],
)
def test_the_token_host_follows_the_api_root(api_url: str, host: str) -> None:
    """`gh` is asked for the token of the host the API root belongs to."""
    assert cli._github_host(api_url) == host


@pytest.mark.timeout(120)
def test_a_second_run_asks_github_nothing(
    tmp_path: pathlib.Path, github: FakeGithubApi
) -> None:
    """A pin answered once is served from the disk cache by the next process."""
    cache = ("--pin-cache-dir", str(tmp_path / "cache"))
    first, _ = _run("compliant", tmp_path, github, *cache)
    assert first == 0
    assert github.requested == [_commit_path(COMMIT)]
    assert any((tmp_path / "cache").rglob(COMMIT)), "the answer went elsewhere"
    github.requested.clear()
    second, document = _run("compliant", tmp_path, github, *cache)
    assert (second, document["verdict"]) == (0, "compliant"), document
    assert github.requested == []


@pytest.mark.timeout(120)
def test_a_tag_object_answer_is_served_from_the_cache_with_its_commit(
    tmp_path: pathlib.Path, github: FakeGithubApi
) -> None:
    """A cached tag object still names the commit it peels to."""
    cache = ("--pin-cache-dir", str(tmp_path / "cache"))
    _run("workflow_tag_object", tmp_path, github, *cache)
    github.requested.clear()
    _, document = _run("workflow_tag_object", tmp_path, github, *cache)
    assert github.requested == []
    [(_, message)] = _pd006(document)
    assert COMMIT in message, message


@pytest.mark.timeout(120)
def test_a_rate_limited_answer_is_not_cached(
    tmp_path: pathlib.Path, github: FakeGithubApi
) -> None:
    """A spent limit is asked again next run, so the pin resolves once it lifts."""
    cache = ("--pin-cache-dir", str(tmp_path / "cache"))
    limited = github.routes[_commit_path(COMMIT)]
    github.routes[_commit_path(COMMIT)] = Reply(429, {"message": "rate limited"})
    _, document = _run("compliant", tmp_path, github, *cache)
    assert [verdict for verdict, _ in _pd006(document)] == ["indeterminate"]
    github.routes[_commit_path(COMMIT)] = limited
    returncode, document = _run("compliant", tmp_path, github, *cache)
    assert (returncode, document["verdict"]) == (0, "compliant"), document
    assert github.requested.count(_commit_path(COMMIT)) == 2


@pytest.mark.timeout(120)
def test_no_pin_cache_neither_reads_nor_writes(
    tmp_path: pathlib.Path, github: FakeGithubApi
) -> None:
    """`--no-pin-cache` asks every time and leaves the directory empty."""
    cache = tmp_path / "cache"
    flags = ("--pin-cache-dir", str(cache), "--no-pin-cache")
    _run("compliant", tmp_path, github, *flags)
    _run("compliant", tmp_path, github, *flags)
    assert github.requested == [_commit_path(COMMIT)] * 2
    assert not cache.exists()


@pytest.mark.timeout(120)
def test_a_cached_answer_is_not_reused_for_another_api_root(
    tmp_path: pathlib.Path, github: FakeGithubApi
) -> None:
    """The same slug and SHA on another host is asked again, not assumed."""
    cache = ("--pin-cache-dir", str(tmp_path / "cache"))
    _run("compliant", tmp_path, github, *cache)
    other_root = github.url.replace("127.0.0.1", "localhost")
    checkout = tmp_path / "other"
    checkout.mkdir()
    GENERATE.lay_out(GENERATE.SCENARIOS["compliant"], checkout)
    argv = ["artefact", "rule", "run", RULE_ID, "--repo", str(checkout)]
    with contextlib.redirect_stdout(io.StringIO()):
        returncode = _main([*argv, "--github-api-url", other_root, *cache])
    assert returncode == 0
    assert github.requested == [_commit_path(COMMIT)] * 2
