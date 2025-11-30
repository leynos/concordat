"""Rendering helpers for persistence artifacts."""

from __future__ import annotations

import typing as typ

if typ.TYPE_CHECKING:
    from concordat.persistence.models import PersistenceDescriptor


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
