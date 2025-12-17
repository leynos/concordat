"""Unit tests for persistence endpoint URL normalization."""

from __future__ import annotations

from concordat.persistence.endpoints import normalize_endpoint_url


def test_normalize_endpoint_url_strips_whitespace() -> None:
    """Strip leading/trailing whitespace before normalising."""
    normalized = normalize_endpoint_url("  s3.fr-par.scw.cloud  ")
    assert normalized == "https://s3.fr-par.scw.cloud"


def test_normalize_endpoint_url_empty_string_returns_empty() -> None:
    """Empty or whitespace-only values should remain empty."""
    assert normalize_endpoint_url("") == ""
    assert normalize_endpoint_url("   ") == ""


def test_normalize_endpoint_url_double_slash_host_uses_https_scheme() -> None:
    """Double-slash URLs should default to HTTPS."""
    normalized = normalize_endpoint_url("//s3.fr-par.scw.cloud")
    assert normalized == "https://s3.fr-par.scw.cloud"


def test_normalize_endpoint_url_schemeless_hostname_gets_https() -> None:
    """Schemeless hostnames should default to HTTPS."""
    normalized = normalize_endpoint_url("s3.fr-par.scw.cloud")
    assert normalized == "https://s3.fr-par.scw.cloud"


def test_normalize_endpoint_url_preserves_existing_schemes() -> None:
    """Existing schemes should be preserved verbatim."""
    assert (
        normalize_endpoint_url("https://s3.fr-par.scw.cloud")
        == "https://s3.fr-par.scw.cloud"
    )
    assert (
        normalize_endpoint_url("http://s3.fr-par.scw.cloud")
        == "http://s3.fr-par.scw.cloud"
    )
    assert (
        normalize_endpoint_url("ftp://s3.fr-par.scw.cloud")
        == "ftp://s3.fr-par.scw.cloud"
    )


def test_normalize_endpoint_url_respects_custom_default_scheme() -> None:
    """Custom default_scheme should be used for schemeless inputs."""
    normalized = normalize_endpoint_url(
        "s3.fr-par.scw.cloud",
        default_scheme="http",
    )
    assert normalized == "http://s3.fr-par.scw.cloud"
