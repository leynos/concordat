"""Data structures and shared constants for persistence workflow."""
# ruff: noqa: TRY003

from __future__ import annotations

import dataclasses
import typing as typ

from ruamel.yaml import YAML

from concordat.errors import ConcordatError

if typ.TYPE_CHECKING:
    import datetime as dt
    from pathlib import Path

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


class S3Client(typ.Protocol):
    """Protocol capturing the minimal S3 operations needed for persistence."""

    def get_bucket_versioning(self, **kwargs: object) -> dict[str, typ.Any]:
        """Return bucket versioning status."""

    def put_object(self, **kwargs: object) -> dict[str, typ.Any]:
        """Write an object to the bucket."""

    def delete_object(self, **kwargs: object) -> dict[str, typ.Any]:
        """Delete an object from the bucket."""


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
    pr_opener: typ.Callable[..., str | None] | None = None
    fmt_runner: typ.Callable[[Path], None] | None = None
    timestamp_factory: typ.Callable[[], dt.datetime] | None = None
    allow_insecure_endpoint: bool = False
