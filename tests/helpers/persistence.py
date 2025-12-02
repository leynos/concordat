"""Shared helpers for persistence-related test setup."""

from __future__ import annotations

import typing as typ

from concordat.persistence import models as persistence_models

if typ.TYPE_CHECKING:
    from pathlib import Path


def seed_persistence_files(
    repo_path: Path,
    *,
    enabled: bool = True,
    backend_filename: str = "core.tfbackend",
    backend_config_path: str | None = None,
    create_backend_file: bool = True,
) -> Path:
    """Write backend config and manifest into a repository for tests."""
    backend_dir = repo_path / "backend"
    backend_dir.mkdir(parents=True, exist_ok=True)

    backend_path = backend_dir / backend_filename
    if create_backend_file:
        backend_path.write_text(
            'bucket = "df12-tfstate"\n'
            'key    = "estates/example/main/terraform.tfstate"\n'
            'region = "fr-par"\n',
            encoding="utf-8",
        )

    manifest = {
        "schema_version": persistence_models.PERSISTENCE_SCHEMA_VERSION,
        "enabled": enabled,
        "bucket": "df12-tfstate",
        "key_prefix": "estates/example/main",
        "key_suffix": "terraform.tfstate",
        "region": "fr-par",
        "endpoint": "https://s3.fr-par.scw.cloud",
        "backend_config_path": backend_config_path or f"backend/{backend_path.name}",
    }

    with (backend_dir / "persistence.yaml").open("w", encoding="utf-8") as handle:
        persistence_models._yaml.dump(manifest, handle)

    return backend_path
