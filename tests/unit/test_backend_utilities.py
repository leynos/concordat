"""Unit tests for backend utility helpers."""

from __future__ import annotations

import pytest

from concordat.estate_execution import _build_object_key
from concordat.persistence.models import PersistenceDescriptor


@pytest.mark.parametrize(
    ("key_prefix", "key_suffix", "expected"),
    [
        ("prefix", "suffix", "prefix/suffix"),
        ("prefix/", "suffix", "prefix/suffix"),
        ("prefix", "/suffix", "prefix/suffix"),
        ("prefix/", "/suffix", "prefix/suffix"),
        ("", "suffix", "suffix"),
        ("", "/suffix", "suffix"),
        ("prefix", "", "prefix/"),
        ("prefix/", "", "prefix/"),
    ],
)
def test_build_object_key_handles_slashes(
    key_prefix: str, key_suffix: str, expected: str
) -> None:
    """_build_object_key normalises leading/trailing slashes and empties."""
    descriptor = PersistenceDescriptor(
        schema_version=1,
        enabled=True,
        bucket="bucket",
        key_prefix=key_prefix,
        key_suffix=key_suffix,
        region="region",
        endpoint="endpoint",
        backend_config_path="backend/core.tfbackend",
        notification_topic=None,
    )

    assert _build_object_key(descriptor) == expected
