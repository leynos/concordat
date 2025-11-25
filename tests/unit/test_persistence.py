"""Unit tests for estate persistence helpers."""

from __future__ import annotations

import typing as typ

import pytest

from concordat import persistence

if typ.TYPE_CHECKING:
    from pathlib import Path


def test_render_tfbackend_uses_scaleway_shape(tmp_path: Path) -> None:
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
        with pytest.raises(persistence.PersistenceError) as caught:
            persistence._validate_inputs(descriptor, key_suffix)
        assert message in str(caught.value)
    else:
        persistence._validate_inputs(descriptor, key_suffix)


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
