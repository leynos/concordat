"""Behavioural tests for concordat estate persistence."""

from __future__ import annotations

import dataclasses
import io
import shlex
import shutil
import typing as typ
from contextlib import redirect_stdout
from pathlib import Path

import pygit2
import pytest
import pytest_bdd.parsers as parsers
from botocore import exceptions as boto_exceptions
from pytest_bdd import given, scenarios, then, when
from ruamel.yaml import YAML

import concordat.persistence as persistence
from concordat import cli
from concordat.errors import ConcordatError
from concordat.estate import EstateRecord, register_estate
from concordat.estate_execution import cache_root

from .conftest import RunResult

scenarios("features/persist.feature")

_yaml = YAML(typ="safe")


@dataclasses.dataclass(frozen=True)
class PromptValues:
    """Container for prompt responses used by persistence scenarios."""

    bucket: str
    region: str
    endpoint: str
    prefix: str
    suffix: str

    def populate_queue(self, queue: list[str]) -> None:
        """Populate the prompt queue with stored values."""
        queue[:] = [self.bucket, self.region, self.endpoint, self.prefix, self.suffix]


@pytest.fixture(autouse=True)
def deterministic_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep persistence branches deterministic for assertions."""
    monkeypatch.setattr(
        persistence,
        "_branch_name",
        lambda *args, **kwargs: "estate/persist-test",
    )


@pytest.fixture
def prompt_queue(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Provide a mutable queue for interactive prompt responses."""
    queue: list[str] = []

    def fake_input(_: str) -> str:
        return queue.pop(0) if queue else ""

    monkeypatch.setattr("builtins.input", fake_input)
    return queue


class FakeS3Client:
    """Stub S3 client for persistence tests."""

    def __init__(self) -> None:
        """Initialise the fake client."""
        self.status = "Enabled"
        self.put_keys: list[tuple[str, str]] = []
        self.delete_keys: list[tuple[str, str]] = []
        self.raise_on_versioning = False
        self.raise_on_put = False
        self.raise_on_delete = False

    def get_bucket_versioning(self, **kwargs: object) -> dict[str, str]:
        """Return the configured versioning status."""
        if self.raise_on_versioning:
            raise boto_exceptions.BotoCoreError
        return {"Status": self.status}

    def put_object(self, **kwargs: object) -> dict[str, str]:
        """Record a write operation."""
        if self.raise_on_put:
            raise boto_exceptions.BotoCoreError
        bucket = typ.cast("str", kwargs.get("Bucket", ""))  # type: ignore[index]
        key = typ.cast("str", kwargs.get("Key", ""))  # type: ignore[index]
        self.put_keys.append((bucket, key))
        return {}

    def delete_object(self, **kwargs: object) -> dict[str, str]:
        """Record a delete operation."""
        if self.raise_on_delete:
            raise boto_exceptions.BotoCoreError
        bucket = typ.cast("str", kwargs.get("Bucket", ""))  # type: ignore[index]
        key = typ.cast("str", kwargs.get("Key", ""))  # type: ignore[index]
        self.delete_keys.append((bucket, key))
        return {}


@pytest.fixture
def fake_s3(monkeypatch: pytest.MonkeyPatch) -> FakeS3Client:
    """Replace the default S3 client factory with a stub."""
    client = FakeS3Client()
    monkeypatch.setattr(
        persistence,
        "_default_s3_client_factory",
        lambda region, endpoint: client,
    )
    return client


@pytest.fixture
def fake_s3_client(fake_s3: FakeS3Client) -> FakeS3Client:
    """Expose the default fake S3 client for assertions."""
    return fake_s3


@pytest.fixture
def pr_stub(monkeypatch: pytest.MonkeyPatch) -> dict[str, typ.Any]:
    """Capture pull request creation attempts."""
    log: dict[str, typ.Any] = {}

    def opener(request: persistence.PullRequestRequest) -> str:
        log["branch"] = request.branch_name
        log["bucket"] = request.descriptor.bucket
        log["key_suffix"] = request.key_suffix
        log["token"] = request.github_token
        return "https://example.test/pr/1"

    monkeypatch.setattr(persistence, "_open_pr", opener)
    return log


@pytest.fixture
def config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Configure isolated XDG paths for concordat."""
    config_home = tmp_path / "config"
    cache_home = tmp_path / "cache"
    config_home.mkdir()
    cache_home.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache_home))
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "FAKEKEYID")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "super-secret-key")
    return config_home


def _run_cli(arguments: list[str]) -> RunResult:
    buffer_out = io.StringIO()
    try:
        with redirect_stdout(buffer_out):
            result = cli.app(
                arguments,
                exit_on_error=False,
                print_error=False,
            )
    except ConcordatError as error:
        return RunResult(stdout=buffer_out.getvalue(), stderr=str(error), returncode=1)
    except SystemExit as exc:
        return RunResult(
            stdout=buffer_out.getvalue(), stderr="", returncode=int(exc.code or 0)
        )
    else:
        return RunResult(
            stdout=buffer_out.getvalue(), stderr="", returncode=int(result or 0)
        )


def _seed_estate_remote(root: Path) -> Path:
    source = root / "estate-source"
    shutil.copytree(Path(__file__).resolve().parents[2] / "platform-standards", source)
    repo = pygit2.init_repository(str(source), initial_head="main")
    repo.index.add_all()
    repo.index.write()
    tree_oid = repo.index.write_tree()
    signature = pygit2.Signature("Test", "test@example.com")
    repo.create_commit(
        "refs/heads/main",
        signature,
        signature,
        "seed estate",
        tree_oid,
        [],
    )

    bare = root / "estate-remote.git"
    pygit2.init_repository(str(bare), bare=True)
    remote = repo.remotes.create("origin", str(bare))
    remote.push(["refs/heads/main:refs/heads/main"])
    pygit2.Repository(str(bare)).set_head("refs/heads/main")
    return bare


@given("an isolated concordat config directory", target_fixture="config_path")
def given_config_dir(config_dir: Path) -> Path:
    """Expose the config directory path for downstream steps."""
    return config_dir


@given(
    parsers.cfparse('an estate repository with alias "{alias}"'),
    target_fixture="estate_alias",
)
def given_estate_repo(alias: str, tmp_path: Path, config_dir: Path) -> str:
    """Create a local estate repository and register it."""
    remote = _seed_estate_remote(tmp_path)
    config_path = config_dir / "concordat" / "config.yaml"
    register_estate(
        EstateRecord(
            alias=alias,
            repo_url=str(remote),
            github_owner="example",
        ),
        config_path=config_path,
        set_active_if_missing=True,
    )
    return alias


@given("pull requests are stubbed")
def given_pr_stubbed(pr_stub: dict[str, typ.Any]) -> None:
    """Ensure PR attempts are recorded via stub."""
    return


@given("GITHUB_TOKEN is unset")
def given_github_token_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear GitHub token to allow PR-less persistence."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)


@given("bucket versioning check fails")
def given_versioning_failure(fake_s3: FakeS3Client) -> None:
    """Force the fake S3 client to fail versioning checks."""
    fake_s3.raise_on_versioning = True


@given("bucket write permission check fails")
def given_write_failure(fake_s3: FakeS3Client) -> None:
    """Force the fake S3 client to fail write/delete probes."""
    fake_s3.raise_on_put = True
    fake_s3.raise_on_delete = True


@given(parsers.cfparse('bucket versioning status is "{status}"'))
def given_bucket_versioning(fake_s3: FakeS3Client, status: str) -> None:
    """Set the fake bucket versioning status."""
    fake_s3.status = status


@given(parsers.cfparse('persistence prompts "{values}"'))
@when(parsers.cfparse('persistence prompts "{values}"'))
@then(parsers.cfparse('persistence prompts "{values}"'))
def given_prompt_values(
    values: str,
    prompt_queue: list[str],
) -> None:
    """Populate prompt responses for the persistence workflow."""
    parts = [part.strip() for part in values.split(",")]
    if len(parts) != 5:
        detail = (
            "Expected 5 prompt values (bucket, region, endpoint, prefix, suffix), "
            f"got {len(parts)}"
        )
        raise ValueError(detail)

    prompt_values = PromptValues(
        bucket=parts[0],
        region=parts[1],
        endpoint=parts[2],
        prefix=parts[3],
        suffix=parts[4],
    )
    prompt_values.populate_queue(prompt_queue)


@given(parsers.cfparse('GITHUB_TOKEN is set to "{token}"'))
def given_github_token(token: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Force concordat to use the provided GitHub token."""
    monkeypatch.setenv("GITHUB_TOKEN", token)


@when("I run concordat estate persist")
def when_run_persist(cli_invocation: dict[str, RunResult]) -> None:
    """Execute the persistence command."""
    cli_invocation["result"] = _run_cli(["estate", "persist"])


@when(parsers.cfparse('I run concordat estate persist with options "{options}"'))
def when_run_persist_with_options(
    options: str,
    cli_invocation: dict[str, RunResult],
) -> None:
    """Execute the persistence command with extra flags."""
    extra = shlex.split(options)
    cli_invocation["result"] = _run_cli(["estate", "persist", *extra])


@then("the command succeeds")
def then_command_succeeds(cli_invocation: dict[str, RunResult]) -> None:
    """Assert the CLI exited successfully."""
    result = cli_invocation["result"]
    assert result.returncode == 0, result.stderr


@then(parsers.cfparse('the command fails with error containing "{text}"'))
def then_command_fails_with(cli_invocation: dict[str, RunResult], text: str) -> None:
    """Assert the CLI failed with the expected message."""
    result = cli_invocation["result"]
    assert result.returncode != 0
    assert text in result.stderr


def _estate_path(alias: str, relative: str) -> Path:
    return _estate_root(alias) / relative


def _estate_root(alias: str) -> Path:
    return cache_root() / alias


@then(parsers.cfparse('backend file "{relative}" contains "{expected}"'))
def then_backend_contains(
    estate_alias: str,
    relative: str,
    expected: str,
) -> None:
    """Check the backend file content."""
    path = _estate_path(estate_alias, relative)
    assert path.exists()
    contents = path.read_text(encoding="utf-8")
    assert expected.replace('\\"', '"') in contents


@then(parsers.cfparse('backend file "{relative}" is absent'))
def then_backend_absent(estate_alias: str, relative: str) -> None:
    """Ensure the backend file was not created."""
    assert not _estate_path(estate_alias, relative).exists()


@then("persistence manifest is absent")
def then_manifest_absent(estate_alias: str) -> None:
    """Ensure the persistence manifest was not created."""
    path = _estate_path(estate_alias, "backend/persistence.yaml")
    assert not path.exists()


@then("the persistence change is merged into main")
def then_merge_persistence_change(estate_alias: str) -> None:
    """Fast-forward the main branch to include the persistence commit."""
    repository = pygit2.Repository(str(_estate_root(estate_alias)))
    persist_branch = repository.branches.get("estate/persist-test")
    main_branch = repository.branches.get("main")
    assert persist_branch is not None
    assert main_branch is not None
    main_branch.set_target(persist_branch.target)
    repository.checkout(main_branch)
    repository.remotes["origin"].push(
        ["+refs/heads/main:refs/heads/main"],
    )


@then(parsers.cfparse('persistence manifest records bucket "{bucket}"'))
def then_manifest_bucket(estate_alias: str, bucket: str) -> None:
    """Assert the manifest records the expected bucket."""
    path = _estate_path(estate_alias, "backend/persistence.yaml")
    assert path.exists()
    data = _yaml.load(path.read_text(encoding="utf-8")) or {}
    assert data.get("bucket") == bucket


@then(
    parsers.cfparse(
        "the persistence permissions probe writes and deletes a single object to "
        'bucket "{bucket}" with key "{key}"'
    )
)
def then_persistence_probe_uses_expected_key(
    fake_s3_client: FakeS3Client,
    bucket: str,
    key: str,
) -> None:
    """Assert the permissions probe uses the expected bucket/key combination."""
    assert len(fake_s3_client.put_keys) == 1, fake_s3_client.put_keys
    assert len(fake_s3_client.delete_keys) == 1, fake_s3_client.delete_keys
    assert fake_s3_client.put_keys[0] == (bucket, key)
    assert fake_s3_client.delete_keys[0] == (bucket, key)


@then("no pull request was attempted")
def then_no_pr(pr_stub: dict[str, typ.Any]) -> None:
    """Ensure PR opener was not invoked."""
    assert not pr_stub


@then("credentials are not written to the backend files")
def then_no_credentials_leaked(estate_alias: str) -> None:
    """Ensure secret-looking values are absent from persisted files."""
    secret = "super-secret-key"  # noqa: S105
    backend = _estate_path(estate_alias, "backend/core.tfbackend")
    manifest = _estate_path(estate_alias, "backend/persistence.yaml")
    combined = ""
    if backend.exists():
        combined += backend.read_text(encoding="utf-8")
    if manifest.exists():
        combined += manifest.read_text(encoding="utf-8")
    assert secret not in combined
