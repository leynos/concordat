"""Remote state persistence workflow for estates."""
# ruff: noqa: TRY003

from __future__ import annotations

import dataclasses
import datetime as dt
import os
import textwrap
import typing as typ
from pathlib import Path

import boto3
import github3
import pygit2
from botocore import exceptions as boto_exceptions
from botocore.config import Config as BotoConfig
from ruamel.yaml import YAML

from .errors import ConcordatError
from .gitutils import build_remote_callbacks
from .platform_standards import parse_github_slug

if typ.TYPE_CHECKING:
    from .estate import EstateRecord


class S3Client(typ.Protocol):
    """Protocol capturing the minimal S3 operations needed for persistence."""

    def get_bucket_versioning(self, **kwargs: object) -> dict[str, typ.Any]:
        """Return bucket versioning status."""

    def put_object(self, **kwargs: object) -> dict[str, typ.Any]:
        """Write an object to the bucket."""

    def delete_object(self, **kwargs: object) -> dict[str, typ.Any]:
        """Delete an object from the bucket."""


PERSISTENCE_SCHEMA_VERSION = 1
DEFAULT_KEY_FILENAME = "terraform.tfstate"
MANIFEST_FILENAME = "backend/persistence.yaml"
BACKEND_DIRNAME = "backend"
PERSISTENCE_CHECK_SUFFIX = "concordat-tfstate-check"

_yaml = YAML(typ="safe")
_yaml.version = (1, 2)
_yaml.default_flow_style = False


class PersistenceError(ConcordatError):
    """Raised when persisting remote state configuration fails."""


@dataclasses.dataclass(frozen=True)
class PersistenceDescriptor:
    """Machine-readable manifest describing the remote state backend."""

    schema_version: int
    enabled: bool
    bucket: str
    key_prefix: str
    region: str
    endpoint: str
    backend_config_path: str
    notification_topic: str | None = None

    @classmethod
    def from_yaml(cls, path: Path) -> PersistenceDescriptor | None:
        """Load the descriptor from disk if present."""
        if not path.exists():
            return None
        loaded = _yaml.load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(loaded, dict):
            raise PersistenceError(f"Invalid persistence manifest at {path}")
        schema_version = int(loaded.get("schema_version", 0))
        if schema_version > PERSISTENCE_SCHEMA_VERSION:
            raise PersistenceError(
                "Unsupported persistence manifest "
                f"schema_version={schema_version} at {path}; maximum supported "
                f"schema_version is {PERSISTENCE_SCHEMA_VERSION}"
            )
        enabled = bool(loaded.get("enabled", False))
        bucket = str(loaded.get("bucket", "")).strip()
        key_prefix = str(loaded.get("key_prefix", "")).strip()
        region = str(loaded.get("region", "")).strip()
        endpoint = str(loaded.get("endpoint", "")).strip()
        backend_path = str(loaded.get("backend_config_path", "")).strip()
        notification_topic = loaded.get("notification_topic")
        return cls(
            schema_version=schema_version,
            enabled=enabled,
            bucket=bucket,
            key_prefix=key_prefix,
            region=region,
            endpoint=endpoint,
            backend_config_path=backend_path,
            notification_topic=(
                str(notification_topic) if notification_topic is not None else None
            ),
        )

    def to_dict(self) -> dict[str, typ.Any]:
        """Serialise the descriptor to a YAML-friendly mapping."""
        payload: dict[str, typ.Any] = {
            "schema_version": self.schema_version,
            "enabled": self.enabled,
            "bucket": self.bucket,
            "key_prefix": self.key_prefix,
            "region": self.region,
            "endpoint": self.endpoint,
            "backend_config_path": self.backend_config_path,
        }
        if self.notification_topic is not None:
            payload["notification_topic"] = self.notification_topic
        return payload


@dataclasses.dataclass(frozen=True)
class PersistenceResult:
    """Outcome of running the persistence workflow."""

    backend_path: Path
    manifest_path: Path
    branch: str | None
    pr_url: str | None
    updated: bool
    message: str

    def render(self) -> str:
        """Return a short human readable summary."""
        action = "updated" if self.updated else "unchanged"
        parts = [f"{action} {self.backend_path} and {self.manifest_path}"]
        if self.message:
            parts.append(self.message)
        if self.pr_url:
            parts.append(f"PR: {self.pr_url}")
        elif self.branch:
            parts.append(f"branch: {self.branch}")
        return "; ".join(parts)


@dataclasses.dataclass(frozen=True)
class PersistenceFiles:
    """Backend and manifest file contents to persist."""

    backend_path: Path
    backend_contents: str
    manifest_path: Path
    manifest_contents: dict[str, typ.Any]


@dataclasses.dataclass(frozen=True)
class PersistenceOptions:
    """Optional configuration and callbacks for persistence workflow."""

    force: bool = False
    github_token: str | None = None
    input_func: typ.Callable[[str], str] | None = None
    s3_client_factory: typ.Callable[[str, str], S3Client] | None = None
    pr_opener: typ.Callable[[PullRequestRequest], str | None] | None = None
    fmt_runner: typ.Callable[[Path], None] | None = None
    timestamp_factory: typ.Callable[[], dt.datetime] | None = None
    allow_insecure_endpoint: bool = False


@dataclasses.dataclass(frozen=True)
class PullRequestRequest:
    """Information needed to create a persistence pull request."""

    record: EstateRecord
    branch_name: str
    descriptor: PersistenceDescriptor
    key_suffix: str
    github_token: str | None


def _setup_persistence_environment(
    record: EstateRecord,
) -> tuple[Path, pygit2.Repository, Path, Path]:
    """Load a clean estate workspace and derive persistence file paths."""
    workdir = _load_clean_estate(record)
    repository = pygit2.Repository(str(workdir))
    manifest_path = workdir / MANIFEST_FILENAME
    backend_path = workdir / BACKEND_DIRNAME / f"{record.alias}.tfbackend"
    return workdir, repository, manifest_path, backend_path


def persist_estate(
    record: EstateRecord,
    options: PersistenceOptions | None = None,
) -> PersistenceResult:
    """Configure remote state for an estate and open a pull request."""
    opts = options or PersistenceOptions()
    workdir, repository, manifest_path, backend_path = _setup_persistence_environment(
        record
    )

    input_func = opts.input_func or input
    s3_client_factory = opts.s3_client_factory or _default_s3_client_factory

    existing_descriptor = PersistenceDescriptor.from_yaml(manifest_path)
    defaults = _defaults_from(record, existing_descriptor)
    prompts = _collect_user_inputs(defaults, input_func)
    descriptor = _build_descriptor(prompts, backend_path)

    if not opts.force:
        _guard_existing_files(
            backend_path,
            manifest_path,
            descriptor,
            prompts["key_suffix"],
        )

    _validate_inputs(
        descriptor,
        prompts["key_suffix"],
        allow_insecure_endpoint=opts.allow_insecure_endpoint,
    )
    _validate_bucket(descriptor, prompts["key_suffix"], s3_client_factory)

    backend_contents = _render_tfbackend(descriptor, prompts["key_suffix"])
    manifest_contents = descriptor.to_dict()

    files = PersistenceFiles(
        backend_path=backend_path,
        backend_contents=backend_contents,
        manifest_path=manifest_path,
        manifest_contents=manifest_contents,
    )

    early_result = _write_files_and_check_for_changes(
        files,
        force=opts.force,
    )
    if early_result:
        return early_result

    if opts.fmt_runner:
        opts.fmt_runner(workdir)

    branch_name = _commit_changes(
        repository,
        record.branch,
        [backend_path, manifest_path],
        timestamp_factory=opts.timestamp_factory,
    )
    _push_branch(repository, branch_name, record.repo_url)

    request = PullRequestRequest(
        record=record,
        branch_name=branch_name,
        descriptor=descriptor,
        key_suffix=prompts["key_suffix"],
        github_token=opts.github_token,
    )
    pr_url = _open_pr_if_configured(request, opts.pr_opener)

    return PersistenceResult(
        backend_path=backend_path,
        manifest_path=manifest_path,
        branch=branch_name,
        pr_url=pr_url,
        updated=True,
        message=_build_result_message(pr_url),
    )


def _load_clean_estate(record: EstateRecord) -> Path:
    from .estate_execution import ensure_estate_cache

    workdir = ensure_estate_cache(record)
    repository = pygit2.Repository(str(workdir))
    status = repository.status()
    if dirty := [
        path for path, flags in status.items() if flags != pygit2.GIT_STATUS_CURRENT
    ]:
        formatted = ", ".join(sorted(dirty))
        raise PersistenceError(
            f"Estate cache for {record.alias!r} has uncommitted changes: {formatted}"
        )
    return workdir


def _defaults_from(
    record: EstateRecord,
    descriptor: PersistenceDescriptor | None,
) -> dict[str, str]:
    owner = record.github_owner or "unknown-owner"
    base_prefix = f"estates/{owner}/{record.branch}"
    return {
        "bucket": descriptor.bucket if descriptor else "",
        "region": descriptor.region if descriptor else "",
        "endpoint": descriptor.endpoint if descriptor else "",
        "key_prefix": descriptor.key_prefix if descriptor else base_prefix,
        "key_suffix": DEFAULT_KEY_FILENAME,
    }


def _collect_user_inputs(
    defaults: dict[str, str],
    input_func: typ.Callable[[str], str],
) -> dict[str, str]:
    return {
        "bucket": _prompt_with_default("Bucket", defaults["bucket"], input_func),
        "region": _prompt_with_default("Region", defaults["region"], input_func),
        "endpoint": _prompt_with_default("Endpoint", defaults["endpoint"], input_func),
        "key_prefix": _prompt_with_default(
            "Key prefix", defaults["key_prefix"], input_func
        ),
        "key_suffix": _prompt_with_default(
            "Key suffix", defaults["key_suffix"], input_func
        ),
    }


def _prompt_with_default(
    label: str,
    default: str,
    input_func: typ.Callable[[str], str],
) -> str:
    suffix = f" [{default}]" if default else ""
    if response := input_func(f"{label}{suffix}: ").strip():
        return response
    if default:
        return default
    raise PersistenceError(f"{label} is required.")


def _build_descriptor(
    prompts: dict[str, str],
    backend_path: Path,
) -> PersistenceDescriptor:
    return PersistenceDescriptor(
        schema_version=PERSISTENCE_SCHEMA_VERSION,
        enabled=True,
        bucket=prompts["bucket"],
        key_prefix=prompts["key_prefix"],
        region=prompts["region"],
        endpoint=prompts["endpoint"],
        backend_config_path=str(Path(BACKEND_DIRNAME) / backend_path.name),
    )


def _validate_inputs(
    descriptor: PersistenceDescriptor,
    key_suffix: str,
    *,
    allow_insecure_endpoint: bool = False,
) -> None:
    """Validate descriptor fields and endpoint constraints."""
    _validate_path_safety(descriptor.key_prefix, "Key prefix")
    _validate_path_safety(key_suffix, "Key suffix")
    _validate_key_suffix_not_empty(key_suffix)
    _validate_required_fields(descriptor)
    _validate_endpoint_protocol(
        descriptor.endpoint,
        allow_insecure_endpoint=allow_insecure_endpoint,
    )


def _validate_path_safety(path: str, field_name: str) -> None:
    """Ensure path segments do not include traversal elements."""
    if ".." in path.split("/"):
        raise PersistenceError(f"{field_name} may not include directory traversals.")


def _validate_key_suffix_not_empty(key_suffix: str) -> None:
    """Ensure the key suffix is not empty or whitespace only."""
    if not key_suffix.strip():
        raise PersistenceError("Key suffix is required.")


def _validate_required_fields(descriptor: PersistenceDescriptor) -> None:
    """Ensure required descriptor fields are populated."""
    if not descriptor.bucket:
        raise PersistenceError("Bucket is required.")
    if not descriptor.region:
        raise PersistenceError("Region is required.")


def _is_secure_endpoint(endpoint: str) -> bool:
    """Check if endpoint uses HTTPS protocol."""
    return endpoint.startswith("https://")


def _is_allowed_insecure_endpoint(endpoint: str, *, allow_insecure: bool) -> bool:
    """Check if endpoint uses HTTP and insecure endpoints are allowed."""
    return allow_insecure and endpoint.startswith("http://")


def _has_protocol_scheme(endpoint: str) -> bool:
    """Check if endpoint includes a protocol scheme."""
    return "://" in endpoint


def _validate_endpoint_protocol(
    endpoint: str, *, allow_insecure_endpoint: bool = False
) -> None:
    """Ensure endpoints use HTTPS unless explicitly allowed for dev use."""
    if not endpoint.strip():
        raise PersistenceError("Endpoint is required.")
    if _is_secure_endpoint(endpoint):
        return
    if _is_allowed_insecure_endpoint(endpoint, allow_insecure=allow_insecure_endpoint):
        return
    if not _has_protocol_scheme(endpoint):
        raise PersistenceError(
            "Endpoint must include an https:// scheme (for example, "
            "https://s3.example.com)."
        )
    raise PersistenceError(
        "Endpoint must use HTTPS (for example, https://s3.example.com)."
    )


def _validate_bucket(
    descriptor: PersistenceDescriptor,
    key_suffix: str,
    s3_client_factory: typ.Callable[[str, str], S3Client],
) -> None:
    client = s3_client_factory(descriptor.region, descriptor.endpoint)
    status = _bucket_versioning_status(client, descriptor.bucket)
    if status != "Enabled":
        message = (
            f"Bucket {descriptor.bucket!r} must enable versioning "
            f"(status: {status or 'unknown'})."
        )
        raise PersistenceError(message)
    _exercise_write_permissions(
        client,
        descriptor.bucket,
        f"{descriptor.key_prefix.rstrip('/')}/{key_suffix.lstrip('/')}",
    )


def _bucket_versioning_status(client: S3Client, bucket: str) -> str | None:
    try:
        response = client.get_bucket_versioning(Bucket=bucket)
    except boto_exceptions.BotoCoreError as error:
        raise PersistenceError(f"Failed to query bucket versioning: {error}") from error
    except boto_exceptions.ClientError as error:  # type: ignore[attr-defined]
        raise PersistenceError(f"Failed to query bucket versioning: {error}") from error
    status = response.get("Status")
    return str(status) if status is not None else None


def _exercise_write_permissions(client: S3Client, bucket: str, key: str) -> None:
    probe_key = f"{key}.{PERSISTENCE_CHECK_SUFFIX}"
    try:
        client.put_object(Bucket=bucket, Key=probe_key, Body=b"")
        client.delete_object(Bucket=bucket, Key=probe_key)
    except boto_exceptions.BotoCoreError as error:
        raise PersistenceError(f"Bucket permissions check failed: {error}") from error
    except boto_exceptions.ClientError as error:  # type: ignore[attr-defined]
        raise PersistenceError(f"Bucket permissions check failed: {error}") from error


def _render_tfbackend(
    descriptor: PersistenceDescriptor,
    key_suffix: str,
) -> str:
    key = f"{descriptor.key_prefix.rstrip('/')}/{key_suffix.lstrip('/')}"
    lines = [
        "# Scaleway Object Storage backend for the concordat estate stack.",
        "# Do not add credentials here; export SCW_ACCESS_KEY/SCW_SECRET_KEY instead.",
        f'bucket                      = "{descriptor.bucket}"',
        f'key                         = "{key}"',
        f'region                      = "{descriptor.region}"',
        f'endpoints                   = {{ s3 = "{descriptor.endpoint}" }}',
        "use_path_style              = true",
        "skip_region_validation      = true",
        "skip_requesting_account_id  = true",
        "skip_credentials_validation = true",
        "",
    ]
    return "\n".join(lines)


def _write_files_and_check_for_changes(
    files: PersistenceFiles,
    *,
    force: bool,
) -> PersistenceResult | None:
    """Write backend and manifest files; return early result if unchanged."""
    updated = _write_files(files, force=force)
    if updated:
        return None
    return PersistenceResult(
        backend_path=files.backend_path,
        manifest_path=files.manifest_path,
        branch=None,
        pr_url=None,
        updated=False,
        message="backend already configured",
    )


def _open_pr_if_configured(
    request: PullRequestRequest,
    pr_opener: typ.Callable[[PullRequestRequest], str | None] | None,
) -> str | None:
    """Open a pull request if token or custom opener is provided."""
    if not (request.github_token or pr_opener):
        return None
    opener = pr_opener or _open_pr
    return opener(request)


def _build_result_message(pr_url: str | None) -> str:
    """Build the result message based on PR creation status."""
    return "opened persistence pull request" if pr_url else "pushed branch"


def _guard_existing_files(
    backend_path: Path,
    manifest_path: Path,
    descriptor: PersistenceDescriptor,
    key_suffix: str,
) -> None:
    """Ensure existing backend artifacts match the intended contents."""
    _guard_manifest_file(manifest_path, descriptor)
    _guard_backend_file(backend_path, descriptor, key_suffix)


def _guard_manifest_file(
    manifest_path: Path,
    descriptor: PersistenceDescriptor,
) -> None:
    """Refuse to overwrite a manifest that differs from the expected content."""
    if not manifest_path.exists():
        return
    existing = _yaml.load(manifest_path.read_text(encoding="utf-8")) or {}
    expected_manifest = descriptor.to_dict()
    _enforce_existing_policy(
        manifest_path,
        is_same=existing == expected_manifest,
        force=False,
    )


def _guard_backend_file(
    backend_path: Path,
    descriptor: PersistenceDescriptor,
    key_suffix: str,
) -> None:
    """Refuse to overwrite a backend file that differs from the expected content."""
    if not backend_path.exists():
        return
    current = backend_path.read_text(encoding="utf-8")
    desired = _render_tfbackend(descriptor, key_suffix)
    _enforce_existing_policy(
        backend_path,
        is_same=current == desired,
        force=False,
    )


def _write_files(
    files: PersistenceFiles,
    *,
    force: bool,
) -> bool:
    """Write backend and manifest files if changed."""
    backend_changed = _write_if_changed(
        files.backend_path,
        files.backend_contents,
        force=force,
    )
    manifest_changed = _write_manifest_if_changed(
        files.manifest_path,
        files.manifest_contents,
        force=force,
    )
    return backend_changed or manifest_changed


def _write_if_changed(path: Path, contents: str, *, force: bool) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        current = path.read_text(encoding="utf-8")
        should_write = _enforce_existing_policy(
            path,
            is_same=current == contents,
            force=force,
        )
        if not should_write:
            return False
    path.write_text(contents, encoding="utf-8")
    return True


def _write_manifest_if_changed(
    path: Path,
    contents: dict[str, typ.Any],
    *,
    force: bool,
) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        current = _yaml.load(path.read_text(encoding="utf-8")) or {}
        should_write = _enforce_existing_policy(
            path,
            is_same=current == contents,
            force=force,
        )
        if not should_write:
            return False
    with path.open("w", encoding="utf-8") as handle:
        _yaml.dump(contents, handle)
    return True


def _enforce_existing_policy(
    path: Path,
    *,
    is_same: bool,
    force: bool,
) -> bool:
    """Return True if caller should write, False if identical, else raise.

    When ``is_same`` is False and ``force`` is False, raises a PersistenceError
    to protect existing files from accidental overwrite.
    """
    if is_same:
        return False
    if not force:
        raise PersistenceError(f"{path} already exists; rerun with --force to replace.")
    return True


def _commit_changes(
    repository: pygit2.Repository,
    base_branch: str,
    paths: list[Path],
    *,
    timestamp_factory: typ.Callable[[], dt.datetime] | None = None,
) -> str:
    target = repository.revparse_single(f"refs/heads/{base_branch}")
    commit = target.peel(pygit2.Commit)
    branch_name = _branch_name(timestamp_factory)
    if branch_name in repository.branches.local:
        try:
            if repository.head.shorthand == branch_name:
                repository.checkout(f"refs/heads/{base_branch}")
        except pygit2.GitError:
            # Detached head; proceed with deletion after recreating the branch.
            pass
        repository.branches.delete(branch_name)
    new_branch = repository.create_branch(branch_name, commit)
    repository.checkout(new_branch)
    for path in paths:
        rel = os.path.relpath(path, repository.workdir or ".")
        repository.index.add(rel)
    repository.index.write()
    tree_oid = repository.index.write_tree()
    try:
        signature = repository.default_signature
    except KeyError:
        signature = pygit2.Signature("concordat", "concordat@local")
    commit_message = "chore: configure remote state persistence"
    repository.create_commit(
        "HEAD",
        signature,
        signature,
        commit_message,
        tree_oid,
        [commit.id],
    )
    return branch_name


def _branch_name(timestamp_factory: typ.Callable[[], dt.datetime] | None = None) -> str:
    now = timestamp_factory() if timestamp_factory else dt.datetime.now(dt.timezone.utc)
    return f"estate/persist-{now.strftime('%Y%m%d%H%M%S')}"


def _push_branch(repository: pygit2.Repository, branch: str, repo_url: str) -> None:
    callbacks = build_remote_callbacks(repo_url)
    remote = repository.remotes["origin"]
    refspec = f"+refs/heads/{branch}:refs/heads/{branch}"
    remote.push([refspec], callbacks=callbacks)


def _open_pr(request: PullRequestRequest) -> str | None:
    slug = parse_github_slug(request.record.repo_url)
    if not slug or not request.github_token:
        return None
    owner, name = slug.split("/", 1)
    client = github3.login(token=request.github_token)
    gh_repo = client.repository(owner, name)
    title = "Concordat: persist estate remote state"
    key = (
        f"{request.descriptor.key_prefix.rstrip('/')}/{request.key_suffix.lstrip('/')}"
    )
    body = textwrap.dedent(
        f"""
        This pull request enables remote state for the estate.

        - bucket: `{request.descriptor.bucket}`
        - key: `{key}`
        - region: `{request.descriptor.region}`
        - endpoint: `{request.descriptor.endpoint}`

        Credentials are expected via environment variables; none are written to
        the repository.
        """
    ).strip()
    pr = gh_repo.create_pull(
        title,
        base=request.record.branch,
        head=request.branch_name,
        body=body,
    )
    return pr.html_url


def _default_s3_client_factory(region: str, endpoint: str) -> S3Client:
    config = BotoConfig(s3={"addressing_style": "path"})
    return typ.cast(
        "S3Client",
        boto3.client(
            "s3",
            region_name=region,
            endpoint_url=endpoint,
            config=config,
        ),
    )
