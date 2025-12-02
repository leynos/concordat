"""Render helpers for persistence backend files."""

from __future__ import annotations

import concordat.persistence.render as persistence_render
from concordat import persistence


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
