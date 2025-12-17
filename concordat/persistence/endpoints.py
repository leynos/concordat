"""Endpoint URL helpers for persistence workflows."""

from __future__ import annotations


def normalize_endpoint_url(endpoint: str, *, default_scheme: str = "https") -> str:
    """Return an endpoint URL with an explicit scheme.

    Persistence endpoints are typically supplied as hostnames, but boto3 expects
    fully qualified URLs (including scheme). When the user omits a scheme, we
    assume HTTPS by default.
    """
    cleaned = endpoint.strip()
    if not cleaned:
        return ""

    if cleaned.startswith("//"):
        return f"{default_scheme}:{cleaned}"

    if "://" not in cleaned:
        return f"{default_scheme}://{cleaned}"

    return cleaned
