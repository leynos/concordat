"""Unit tests for estate persistence helpers."""

from __future__ import annotations

import dataclasses
import datetime as dt
import typing as typ

import pygit2
import pytest
from botocore import exceptions as boto_exceptions

import concordat.persistence.files as persistence_files
import concordat.persistence.gitops as gitops
import concordat.persistence.models as persistence_models
import concordat.persistence.pr as persistence_pr
import concordat.persistence.render as persistence_render
import concordat.persistence.validation as persistence_validation
import concordat.persistence.workflow as persistence_workflow
from concordat import estate_execution, persistence
from concordat.estate import EstateRecord

if typ.TYPE_CHECKING:
    from pathlib import Path

    from concordat.persistence import S3Client


@dataclasses.dataclass(frozen=True)
class ConflictExpectation:
    """Expected behavior for conflict handling tests."""

    force: bool
    expect_error: bool
    expected_backend: str
    expected_manifest: dict[str, str]


class StubS3:
    """Stub S3 client used in persistence tests."""

    def get_bucket_versioning(self, **kwargs: object) -> dict[str, str]:
        """Return enabled status for bucket versioning."""
        return {"Status": "Enabled"}

    def put_object(self, **kwargs: object) -> dict[str, str]:
        """Simulate writing an object."""
        return {}

    def delete_object(self, **kwargs: object) -> dict[str, str]:
        """Simulate deleting an object."""
        return {}


def _make_repo(root: Path) -> pygit2.Repository:
    repo = pygit2.init_repository(root, initial_head="main")
    readme = root / "README.md"
    readme.parent.mkdir(parents=True, exist_ok=True)
    readme.write_text("seed\n", encoding="utf-8")
    index = repo.index
    index.add("README.md")
    index.write()
    tree_oid = index.write_tree()
    sig = pygit2.Signature("Test", "test@example.com")
    repo.create_commit("refs/heads/main", sig, sig, "seed", tree_oid, [])
    return repo


@pytest.fixture
def stub_s3() -> type[StubS3]:
    """Provide a stub S3 client class for persistence tests."""
    return StubS3


@pytest.fixture
def persist_repo_setup(
    tmp_path: Path,
) -> tuple[Path, pygit2.Repository, Path, EstateRecord]:
    """Create a working repo, bare remote, and estate record."""
    workdir = tmp_path / "workdir"
    repo = _make_repo(workdir)

    bare = tmp_path / "remote.git"
    pygit2.init_repository(str(bare), bare=True)
    upstream = repo.remotes.create("upstream", str(bare))
    upstream.push(["refs/heads/main:refs/heads/main"])

    record = EstateRecord(
        alias="core",
        repo_url=str(bare),
        github_owner="example",
    )
    return workdir, repo, bare, record


@pytest.fixture
def persist_prompts() -> typ.Iterator[str]:
    """Return standard prompt responses for persistence flows."""
    return iter(
        [
            "df12",
            "fr-par",
            "https://s3.fr-par.scw.cloud",
            "estates/example/main",
            "terraform.tfstate",
        ]
    )


@pytest.fixture
def persist_monkeypatch_base(
    monkeypatch: pytest.MonkeyPatch,
    persist_repo_setup: tuple[Path, pygit2.Repository, Path, EstateRecord],
) -> None:
    """Apply shared monkeypatches for persistence repo setup."""
    workdir, _, _, _ = persist_repo_setup
    monkeypatch.setattr(
        estate_execution,
        "ensure_estate_cache",
        lambda _: workdir,
    )
    monkeypatch.setattr(
        gitops,
        "_branch_name",
        lambda *args, **kwargs: "estate/persist-test",
    )


@pytest.fixture
def persist_test_context(
    persist_repo_setup: tuple[Path, pygit2.Repository, Path, EstateRecord],
    persist_prompts: typ.Iterator[str],
    persist_monkeypatch_base: None,
    stub_s3: type,
) -> PersistTestContext:
    """Bundle common fixtures for persist_estate tests."""
    workdir, repo, bare, record = persist_repo_setup
    return PersistTestContext(
        workdir=workdir,
        repo=repo,
        bare=bare,
        record=record,
        prompts=persist_prompts,
        stub_s3=stub_s3,
    )


@pytest.fixture
def conflict_test_setup(tmp_path: Path) -> tuple[Path, Path]:
    """Set up paths with existing content for conflict testing."""
    backend_path = tmp_path / "backend.tfbackend"
    manifest_path = tmp_path / "backend" / "persistence.yaml"
    backend_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    backend_path.write_text("old-backend", encoding="utf-8")
    with manifest_path.open("w", encoding="utf-8") as handle:
        persistence_models._yaml.dump({"bucket": "old"}, handle)
    return backend_path, manifest_path


@pytest.fixture(
    params=[
        ConflictExpectation(
            force=False,
            expect_error=True,
            expected_backend="old-backend",
            expected_manifest={"bucket": "old"},
        ),
        ConflictExpectation(
            force=True,
            expect_error=False,
            expected_backend="new-backend",
            expected_manifest={"bucket": "new"},
        ),
    ],
    ids=["raises_without_force", "overwrites_with_force"],
)
def expectation(request: pytest.FixtureRequest) -> ConflictExpectation:
    """Provide conflict scenarios for write handling."""
    return request.param


def test_render_tfbackend_uses_scaleway_shape() -> None:
    """Rendered tfbackend omits lockfile and records endpoint."""
    descriptor = persistence.PersistenceDescriptor(
        schema_version=persistence.PERSISTENCE_SCHEMA_VERSION,
        enabled=True,
        bucket="df12-tfstate",
        key_prefix="estates/example/main",
        key_suffix="terraform.tfstate",
        region="fr-par",
        endpoint="https://s3.fr-par.scw.cloud",
        backend_config_path="backend/core.tfbackend",
    )
    rendered = persistence_render._render_tfbackend(descriptor, "terraform.tfstate")

    assert "use_lockfile" not in rendered
    assert 'bucket                      = "df12-tfstate"' in rendered
    assert (
        'endpoints                   = { s3 = "https://s3.fr-par.scw.cloud" }'
        in rendered
    )
    assert rendered.rstrip().endswith("skip_credentials_validation = true")


@pytest.mark.parametrize(
    ("bucket", "region", "endpoint", "message"),
    [
        ("", "fr-par", "https://s3.fr-par.scw.cloud", "Bucket is required."),
        ("df12", "", "https://s3.fr-par.scw.cloud", "Region is required."),
        ("df12", "fr-par", "", "Endpoint is required."),
        (
            "df12",
            "fr-par",
            "s3.fr-par.scw.cloud",
            "Endpoint must include an https:// scheme",
        ),
        (
            "df12",
            "fr-par",
            "http://endpoint",
            "Endpoint must use HTTPS",
        ),
        (
            "df12",
            "fr-par",
            "https://endpoint",
            "",
        ),
    ],
)
def test_validate_inputs_enforces_constraints(
    bucket: str,
    region: str,
    endpoint: str,
    message: str,
) -> None:
    """Input validation blocks missing or insecure settings."""
    descriptor = persistence.PersistenceDescriptor(
        schema_version=persistence.PERSISTENCE_SCHEMA_VERSION,
        enabled=True,
        bucket=bucket,
        key_prefix="estates/example/main",
        key_suffix="terraform.tfstate",
        region=region,
        endpoint=endpoint,
        backend_config_path="backend/core.tfbackend",
    )
    key_suffix = "terraform.tfstate"
    if message:
        with pytest.raises(persistence.PersistenceError, match=message):
            persistence_validation._validate_inputs(descriptor, key_suffix)
    else:
        persistence_validation._validate_inputs(descriptor, key_suffix)


@pytest.mark.parametrize(
    ("key_prefix", "key_suffix", "expected_message"),
    [
        ("foo/../bar", "terraform.tfstate", "directory traversals"),
        ("estates/example/main", "   ", "Key suffix is required."),
    ],
    ids=["path_traversal_in_prefix", "empty_key_suffix"],
)
def test_validate_inputs_rejects_invalid_paths(
    key_prefix: str,
    key_suffix: str,
    expected_message: str,
) -> None:
    """Path validation blocks directory traversal and empty key suffix."""
    descriptor = persistence.PersistenceDescriptor(
        schema_version=persistence.PERSISTENCE_SCHEMA_VERSION,
        enabled=True,
        bucket="df12-tfstate",
        key_prefix=key_prefix,
        key_suffix="terraform.tfstate",
        region="fr-par",
        endpoint="https://s3.fr-par.scw.cloud",
        backend_config_path="backend/core.tfbackend",
    )
    with pytest.raises(persistence.PersistenceError) as excinfo:
        persistence_validation._validate_inputs(descriptor, key_suffix)

    assert expected_message in str(excinfo.value)


def test_descriptor_round_trip_from_yaml(tmp_path: Path) -> None:
    """Descriptors load from YAML and round-trip via to_dict."""
    path = tmp_path / "persistence.yaml"
    descriptor = persistence.PersistenceDescriptor(
        schema_version=persistence.PERSISTENCE_SCHEMA_VERSION,
        enabled=True,
        bucket="df12-tfstate",
        key_prefix="estates/example/main",
        key_suffix="terraform.tfstate",
        region="fr-par",
        endpoint="https://s3.fr-par.scw.cloud",
        backend_config_path="backend/core.tfbackend",
        notification_topic="alerts",
    )
    with path.open("w", encoding="utf-8") as handle:
        persistence_models._yaml.dump(descriptor.to_dict(), handle)

    loaded = persistence.PersistenceDescriptor.from_yaml(path)

    assert loaded is not None
    assert loaded.notification_topic == "alerts"
    assert loaded.to_dict() == descriptor.to_dict()


def test_descriptor_from_yaml_rejects_malformed(tmp_path: Path) -> None:
    """Non-mapping manifests are rejected."""
    path = tmp_path / "persistence.yaml"
    path.write_text("- not-a-mapping\n- still-not\n", encoding="utf-8")

    with pytest.raises(
        persistence.PersistenceError, match="Invalid persistence manifest"
    ):
        persistence.PersistenceDescriptor.from_yaml(path)


def test_descriptor_from_yaml_rejects_newer_schema_version(tmp_path: Path) -> None:
    """Newer schema versions are rejected with a clear message."""
    path = tmp_path / "persistence.yaml"
    newer_version = persistence.PERSISTENCE_SCHEMA_VERSION + 1
    manifest = {
        "schema_version": newer_version,
        "enabled": True,
        "bucket": "df12-tfstate",
        "key_prefix": "estates/example/main",
        "key_suffix": "terraform.tfstate",
        "region": "fr-par",
        "endpoint": "https://s3.fr-par.scw.cloud",
        "backend_config_path": "backend/core.tfbackend",
    }
    with path.open("w", encoding="utf-8") as handle:
        persistence_models._yaml.dump(manifest, handle)

    with pytest.raises(persistence.PersistenceError) as excinfo:
        persistence.PersistenceDescriptor.from_yaml(path)

    message = str(excinfo.value)
    assert str(newer_version) in message
    assert "maximum supported" in message


def test_validate_inputs_allows_insecure_endpoint_when_opted_in() -> None:
    """Insecure endpoints are permitted when explicitly allowed."""
    descriptor = persistence.PersistenceDescriptor(
        schema_version=persistence.PERSISTENCE_SCHEMA_VERSION,
        enabled=True,
        bucket="df12-tfstate",
        key_prefix="estates/example/main",
        key_suffix="terraform.tfstate",
        region="fr-par",
        endpoint="http://localhost:9000",
        backend_config_path="backend/core.tfbackend",
    )
    persistence_validation._validate_inputs(
        descriptor,
        "terraform.tfstate",
        allow_insecure_endpoint=True,
    )


def test_write_if_changed_respects_force(tmp_path: Path) -> None:
    """Existing files are not overwritten unless --force is supplied."""
    path = tmp_path / "backend" / "core.tfbackend"
    path.parent.mkdir(parents=True)
    path.write_text("original", encoding="utf-8")

    with pytest.raises(persistence.PersistenceError):
        persistence_files._write_if_changed(path, "updated", force=False)

    assert path.read_text(encoding="utf-8") == "original"

    updated = persistence_files._write_if_changed(path, "updated", force=True)
    assert updated
    assert path.read_text(encoding="utf-8") == "updated"


def test_write_if_changed_noop_when_contents_identical(tmp_path: Path) -> None:
    """Rewriting identical contents is a no-op."""
    path = tmp_path / "backend" / "core.tfbackend"
    path.parent.mkdir(parents=True)
    path.write_text("unchanged", encoding="utf-8")

    result = persistence_files._write_if_changed(path, "unchanged", force=False)
    assert result is False
    assert path.read_text(encoding="utf-8") == "unchanged"


@pytest.mark.parametrize(
    "exception_factory",
    [
        lambda: boto_exceptions.BotoCoreError(),
        lambda: boto_exceptions.ClientError(  # type: ignore[arg-type]
            error_response={
                "Error": {
                    "Code": "AccessDenied",
                    "Message": "Access denied while getting bucket versioning",
                }
            },
            operation_name="GetBucketVersioning",
        ),
    ],
)
def test_bucket_versioning_status_wraps_errors(
    exception_factory: typ.Callable[[], Exception],
) -> None:
    """Versioning failures surface as PersistenceError."""

    class Client:
        def get_bucket_versioning(self, **kwargs: object) -> dict[str, str]:
            raise exception_factory()

    client = typ.cast("S3Client", Client())
    with pytest.raises(persistence.PersistenceError) as excinfo:
        persistence_validation._bucket_versioning_status(client, "bucket")
    assert "Failed to query bucket versioning" in str(excinfo.value)


@pytest.mark.parametrize(
    ("failing_operation", "test_description"),
    [
        ("put", "write probe"),
        ("delete", "delete probe"),
    ],
    ids=["put_object_fails", "delete_object_fails"],
)
def test_exercise_write_permissions_wraps_errors(
    failing_operation: str,
    test_description: str,
) -> None:
    """Write/delete probe failures become PersistenceError."""

    class Client:
        def put_object(self, **kwargs: object) -> dict[str, str]:
            if failing_operation == "put":
                raise boto_exceptions.BotoCoreError
            return {}

        def delete_object(self, **kwargs: object) -> dict[str, str]:
            if failing_operation == "delete":
                raise boto_exceptions.BotoCoreError
            return {}

    client = typ.cast("S3Client", Client())
    with pytest.raises(persistence.PersistenceError) as excinfo:
        persistence_validation._exercise_write_permissions(client, "bucket", "key")
    message = str(excinfo.value)
    assert "Bucket permissions" in message
    assert "failed" in message


def test_write_manifest_if_changed_noop(tmp_path: Path) -> None:
    """Manifest unchanged returns False without writing."""
    path = tmp_path / "backend" / "persistence.yaml"
    path.parent.mkdir(parents=True)
    path.write_text("a: 1\n", encoding="utf-8")
    changed = persistence_files._write_manifest_if_changed(
        path,
        {"a": 1},
        force=False,
    )
    assert changed is False


def test_write_files_handles_conflicts(
    conflict_test_setup: tuple[Path, Path],
    expectation: ConflictExpectation,
) -> None:
    """Writing differing contents handles conflicts per force flag."""
    backend_path, manifest_path = conflict_test_setup

    files = persistence.PersistenceFiles(
        backend_path=backend_path,
        backend_contents="new-backend",
        manifest_path=manifest_path,
        manifest_contents={"bucket": "new"},
    )

    if expectation.expect_error:
        with pytest.raises(persistence.PersistenceError):
            persistence_files._write_files(files, force=expectation.force)
    else:
        changed = persistence_files._write_files(files, force=expectation.force)
        assert changed is True

    assert backend_path.read_text(encoding="utf-8") == expectation.expected_backend
    assert (
        persistence_models._yaml.load(manifest_path.read_text(encoding="utf-8"))
        == expectation.expected_manifest
    )


def test_write_files_and_check_returns_unchanged_result(tmp_path: Path) -> None:
    """When files are identical, early result marks workflow unchanged."""
    backend_path = tmp_path / "backend.tfbackend"
    manifest_path = tmp_path / "backend" / "persistence.yaml"
    backend_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    descriptor = persistence.PersistenceDescriptor(
        schema_version=1,
        enabled=True,
        bucket="df12",
        key_prefix="estates/example/main",
        key_suffix="terraform.tfstate",
        region="fr-par",
        endpoint="https://s3.fr-par.scw.cloud",
        backend_config_path="backend.tfbackend",
    )
    backend_contents = persistence_render._render_tfbackend(
        descriptor, "terraform.tfstate"
    )
    backend_path.write_text(backend_contents, encoding="utf-8")
    with manifest_path.open("w", encoding="utf-8") as handle:
        persistence_models._yaml.dump(descriptor.to_dict(), handle)

    files = persistence.PersistenceFiles(
        backend_path=backend_path,
        backend_contents=backend_contents,
        manifest_path=manifest_path,
        manifest_contents=descriptor.to_dict(),
    )

    result = persistence_files._write_files_and_check_for_changes(files, force=False)
    assert result is not None
    assert result.updated is False
    assert result.message == "backend already configured"


def test_write_files_and_check_returns_none_when_files_updated(
    tmp_path: Path,
) -> None:
    """When files change, helper writes and returns None."""
    backend_path = tmp_path / "backend.tfbackend"
    manifest_path = tmp_path / "backend" / "persistence.yaml"
    backend_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    files = persistence.PersistenceFiles(
        backend_path=backend_path,
        backend_contents="new-backend",
        manifest_path=manifest_path,
        manifest_contents={"bucket": "new"},
    )

    result = persistence_files._write_files_and_check_for_changes(files, force=False)

    assert result is None
    assert backend_path.read_text(encoding="utf-8") == "new-backend"
    assert persistence_models._yaml.load(manifest_path.read_text(encoding="utf-8")) == {
        "bucket": "new"
    }


def test_setup_persistence_environment_rejects_dirty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dirty cached estate raises a PersistenceError."""
    _make_repo(tmp_path)
    readme = tmp_path / "README.md"
    readme.write_text("dirty\n", encoding="utf-8")
    record = EstateRecord(
        alias="core",
        repo_url=str(tmp_path),
        github_owner="example",
    )

    monkeypatch.setattr(
        estate_execution,
        "ensure_estate_cache",
        lambda record: tmp_path,
    )

    with pytest.raises(persistence.PersistenceError):
        persistence_workflow._load_clean_estate(record)


def test_commit_changes_creates_branch(tmp_path: Path) -> None:
    """_commit_changes creates and checks out a persistence branch."""
    repo = _make_repo(tmp_path)
    target_file = tmp_path / "file.txt"
    target_file.write_text("content", encoding="utf-8")
    branch_name = gitops._commit_changes(
        repo,
        "main",
        [target_file],
        timestamp_factory=lambda: dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc),
    )
    assert branch_name.startswith("estate/persist-")
    assert branch_name in repo.branches.local


def test_open_pr_returns_none_without_token() -> None:
    """_open_pr gracefully skips when token missing."""
    descriptor = persistence.PersistenceDescriptor(
        schema_version=1,
        enabled=True,
        bucket="df12",
        key_prefix="estates/example/main",
        key_suffix="terraform.tfstate",
        region="fr-par",
        endpoint="https://s3.fr-par.scw.cloud",
        backend_config_path="backend.tfbackend",
    )
    record = EstateRecord(
        alias="core",
        repo_url="git@github.com:example/core.git",
        github_owner="example",
    )
    context = persistence.PullRequestContext(
        record=record,
        branch_name="branch",
        descriptor=descriptor,
        key_suffix="terraform.tfstate",
        github_token=None,
    )
    result = persistence_pr._open_pr(context)
    assert result is None


def test_persist_estate_uses_env_token_and_remote(
    monkeypatch: pytest.MonkeyPatch,
    persist_test_context: PersistTestContext,
) -> None:
    """persist_estate falls back to GITHUB_TOKEN and respects custom remotes."""
    ctx = persist_test_context

    monkeypatch.setenv("GITHUB_TOKEN", "env-token")

    pr_log: dict[str, str | None] = {}

    def pr_opener(context: persistence.PullRequestContext) -> str:
        pr_log["github_token"] = context.github_token
        pr_log["branch_name"] = context.branch_name
        return "https://example.test/pr/1"

    push_calls: list[tuple[str, str]] = []

    monkeypatch.setattr(
        gitops,
        "_push_branch",
        lambda repository, branch, repo_url: push_calls.append((branch, repo_url)),
    )

    options = persistence.PersistenceOptions(
        input_func=lambda _: next(ctx.prompts),
        s3_client_factory=lambda region, endpoint: ctx.stub_s3(),
        pr_opener=pr_opener,
    )

    result = persistence.persist_estate(ctx.record, options)

    assert push_calls == [("estate/persist-test", str(ctx.bare))]
    assert pr_log["github_token"] == "env-token"  # noqa: S105
    assert result.pr_url == "https://example.test/pr/1"


def test_persist_estate_prefers_explicit_github_token_over_env(
    monkeypatch: pytest.MonkeyPatch,
    persist_test_context: PersistTestContext,
) -> None:
    """Explicit github_token overrides any GITHUB_TOKEN environment value."""
    ctx = persist_test_context

    monkeypatch.setenv("GITHUB_TOKEN", "env-token")

    captured_token: dict[str, str | None] = {"token": None}

    def pr_opener(context: persistence.PullRequestContext) -> str:
        captured_token["token"] = context.github_token
        return "https://example.test/pr/2"

    options = persistence.PersistenceOptions(
        input_func=lambda _: next(ctx.prompts),
        s3_client_factory=lambda region, endpoint: ctx.stub_s3(),
        pr_opener=pr_opener,
        github_token="explicit-token",  # noqa: S106
    )

    persistence.persist_estate(ctx.record, options)

    assert captured_token["token"] == "explicit-token"  # noqa: S105


@dataclasses.dataclass(frozen=True)
class PersistTestContext:
    """Shared context for persist_estate integration-style tests."""

    workdir: Path
    repo: pygit2.Repository
    bare: Path
    record: EstateRecord
    prompts: typ.Iterator[str]
    stub_s3: type
