"""Persistence model serialization and validation tests."""

from __future__ import annotations

from pathlib import Path

import pytest

import concordat.persistence.models as persistence_models
from concordat import persistence


def test_descriptor_round_trip_from_yaml(tmp_path: Path) -> None:
    """Descriptors load from YAML and round-trip via to_dict."""
    tmp_path = Path(tmp_path)
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
    tmp_path = Path(tmp_path)
    path = tmp_path / "persistence.yaml"
    path.write_text("- not-a-mapping\n- still-not\n", encoding="utf-8")

    with pytest.raises(
        persistence.PersistenceError, match="Invalid persistence manifest"
    ):
        persistence.PersistenceDescriptor.from_yaml(path)


def test_descriptor_from_yaml_rejects_newer_schema_version(tmp_path: Path) -> None:
    """Newer schema versions are rejected with a clear message."""
    tmp_path = Path(tmp_path)
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
