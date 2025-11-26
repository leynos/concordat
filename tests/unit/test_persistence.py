"""Unit tests for estate persistence helpers."""

from __future__ import annotations

import datetime as dt
import typing as typ

import pygit2
import pytest
from botocore import exceptions as boto_exceptions

from concordat import estate_execution, persistence
from concordat.estate import EstateRecord

if typ.TYPE_CHECKING:
    from pathlib import Path


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


def test_render_tfbackend_uses_scaleway_shape() -> None:
    """Rendered tfbackend omits lockfile and records endpoint."""
    descriptor = persistence.PersistenceDescriptor(
        schema_version=persistence.PERSISTENCE_SCHEMA_VERSION,
        enabled=True,
        bucket="df12-tfstate",
        key_prefix="estates/example/main",
        region="fr-par",
        endpoint="https://s3.fr-par.scw.cloud",
        backend_config_path="backend/core.tfbackend",
    )
    rendered = persistence._render_tfbackend(descriptor, "terraform.tfstate")

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
        ("df12", "fr-par", "http://insecure", "Endpoint must use HTTPS."),
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
        region=region,
        endpoint=endpoint,
        backend_config_path="backend/core.tfbackend",
    )
    key_suffix = "terraform.tfstate"
    if message:
        with pytest.raises(persistence.PersistenceError, match=message):
            persistence._validate_inputs(descriptor, key_suffix)
    else:
        persistence._validate_inputs(descriptor, key_suffix)


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
        region="fr-par",
        endpoint="https://s3.fr-par.scw.cloud",
        backend_config_path="backend/core.tfbackend",
    )
    with pytest.raises(persistence.PersistenceError) as excinfo:
        persistence._validate_inputs(descriptor, key_suffix)

    assert expected_message in str(excinfo.value)


def test_validate_inputs_allows_insecure_endpoint_when_opted_in() -> None:
    """Insecure endpoints are permitted when explicitly allowed."""
    descriptor = persistence.PersistenceDescriptor(
        schema_version=persistence.PERSISTENCE_SCHEMA_VERSION,
        enabled=True,
        bucket="df12-tfstate",
        key_prefix="estates/example/main",
        region="fr-par",
        endpoint="http://localhost:9000",
        backend_config_path="backend/core.tfbackend",
    )
    persistence._validate_inputs_with_endpoint(
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
        persistence._write_if_changed(path, "updated", force=False)

    assert path.read_text(encoding="utf-8") == "original"

    updated = persistence._write_if_changed(path, "updated", force=True)
    assert updated
    assert path.read_text(encoding="utf-8") == "updated"


def test_write_if_changed_noop_when_contents_identical(tmp_path: Path) -> None:
    """Rewriting identical contents is a no-op."""
    path = tmp_path / "backend" / "core.tfbackend"
    path.parent.mkdir(parents=True)
    path.write_text("unchanged", encoding="utf-8")

    result = persistence._write_if_changed(path, "unchanged", force=False)
    assert result is False
    assert path.read_text(encoding="utf-8") == "unchanged"


def test_bucket_versioning_status_wraps_errors() -> None:
    """Versioning failures surface as PersistenceError."""

    class Client:
        def get_bucket_versioning(self, **kwargs: object) -> dict[str, str]:
            raise boto_exceptions.BotoCoreError

    client = typ.cast("persistence.S3Client", Client())
    with pytest.raises(persistence.PersistenceError) as excinfo:
        persistence._bucket_versioning_status(client, "bucket")
    assert "Failed to query bucket versioning" in str(excinfo.value)


def test_exercise_write_permissions_wraps_errors() -> None:
    """Write/delete probe failures become PersistenceError."""

    class Client:
        def put_object(self, **kwargs: object) -> dict[str, str]:
            raise boto_exceptions.BotoCoreError

        def delete_object(self, **kwargs: object) -> dict[str, str]:
            return {}

    client = typ.cast("persistence.S3Client", Client())
    with pytest.raises(persistence.PersistenceError) as excinfo:
        persistence._exercise_write_permissions(client, "bucket", "key")
    assert "Bucket permissions check failed" in str(excinfo.value)


def test_guard_existing_files_detects_conflict(tmp_path: Path) -> None:
    """Conflicting manifest or backend files raise errors."""
    backend_path = tmp_path / "backend.tfbackend"
    manifest_path = tmp_path / "backend" / "persistence.yaml"
    backend_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    descriptor = persistence.PersistenceDescriptor(
        schema_version=1,
        enabled=True,
        bucket="df12",
        key_prefix="estates/example/main",
        region="fr-par",
        endpoint="https://s3.fr-par.scw.cloud",
        backend_config_path="backend.tfbackend",
    )
    manifest_path.write_text("different: true\n", encoding="utf-8")
    backend_path.write_text("old-backend", encoding="utf-8")
    with pytest.raises(persistence.PersistenceError):
        persistence._guard_existing_files(
            backend_path,
            manifest_path,
            descriptor,
            "terraform.tfstate",
        )


def test_write_manifest_if_changed_noop(tmp_path: Path) -> None:
    """Manifest unchanged returns False without writing."""
    path = tmp_path / "backend" / "persistence.yaml"
    path.parent.mkdir(parents=True)
    path.write_text("a: 1\n", encoding="utf-8")
    changed = persistence._write_manifest_if_changed(
        path,
        {"a": 1},
        force=False,
    )
    assert changed is False


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
        persistence._load_clean_estate(record)


def test_commit_changes_creates_branch(tmp_path: Path) -> None:
    """_commit_changes creates and checks out a persistence branch."""
    repo = _make_repo(tmp_path)
    target_file = tmp_path / "file.txt"
    target_file.write_text("content", encoding="utf-8")
    branch_name = persistence._commit_changes(
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
        region="fr-par",
        endpoint="https://s3.fr-par.scw.cloud",
        backend_config_path="backend.tfbackend",
    )
    record = EstateRecord(
        alias="core",
        repo_url="git@github.com:example/core.git",
        github_owner="example",
    )
    request = persistence.PullRequestRequest(
        record=record,
        branch_name="branch",
        descriptor=descriptor,
        key_suffix="terraform.tfstate",
        github_token=None,
    )
    result = persistence._open_pr(request)
    assert result is None
