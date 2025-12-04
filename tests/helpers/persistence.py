"""Shared helpers for persistence-related test setup."""

from __future__ import annotations

import dataclasses
import typing as typ

from concordat.persistence import models as persistence_models

if typ.TYPE_CHECKING:
    from pathlib import Path


@dataclasses.dataclass(slots=True)
class PersistenceTestConfig:
    """Configuration for seeding persistence test fixtures."""

    enabled: bool = True
    backend_filename: str = "core.tfbackend"
    backend_config_path: str | None = None
    create_backend_file: bool = True


def seed_invalid_persistence_manifest(
    repo_path: Path, contents: str | None = None
) -> Path:
    """Write an intentionally malformed persistence manifest for tests."""
    backend_dir = repo_path / "backend"
    backend_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = backend_dir / "persistence.yaml"

    manifest_contents = contents or "schema_version: 99\n"
    manifest_path.write_text(manifest_contents, encoding="utf-8")
    return manifest_path


def seed_persistence_files(
    repo_path: Path,
    config: PersistenceTestConfig | None = None,
) -> Path:
    """Write backend config and manifest into a repository for tests."""
    if config is None:
        config = PersistenceTestConfig()
    backend_dir = repo_path / "backend"
    backend_dir.mkdir(parents=True, exist_ok=True)

    backend_path = backend_dir / config.backend_filename
    if config.create_backend_file:
        backend_path.write_text(
            'bucket = "df12-tfstate"\n'
            'key    = "estates/example/main/terraform.tfstate"\n'
            'region = "fr-par"\n',
            encoding="utf-8",
        )

    manifest = {
        "schema_version": persistence_models.PERSISTENCE_SCHEMA_VERSION,
        "enabled": config.enabled,
        "bucket": "df12-tfstate",
        "key_prefix": "estates/example/main",
        "key_suffix": "terraform.tfstate",
        "region": "fr-par",
        "endpoint": "https://s3.fr-par.scw.cloud",
        "backend_config_path": config.backend_config_path
        or f"backend/{backend_path.name}",
    }

    with (backend_dir / "persistence.yaml").open("w", encoding="utf-8") as handle:
        persistence_models._yaml.dump(manifest, handle)

    return backend_path
