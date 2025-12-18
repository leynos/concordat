"""GitHub error detection from OpenTofu output.

This module provides functions to detect specific GitHub-related errors in
tofu command output, such as repositories that already exist but are missing
from state, or resources protected by lifecycle.prevent_destroy.
"""

from __future__ import annotations

import re
import typing as typ

_T = typ.TypeVar("_T")

# Markers for detecting GitHub repository existence errors.
_GITHUB_REPO_EXISTS_MARKER = "name already exists on this account"
_GITHUB_PREVENT_DESTROY_MARKERS = (
    "prevent_destroy",
    "instance cannot be destroyed",
)

# Pattern to extract resource address from GitHub "name exists" errors.
_GITHUB_REPO_ADDRESS_PATTERN = re.compile(
    (
        r'vertex\s+"(?P<address>(?:\\\"|[^"])*)"\s+error:'
        r".*name already exists on this account"
    ),
    re.IGNORECASE,
)

# Pattern to extract repository slug from terraform resource addresses.
_GITHUB_REPO_SLUG_FROM_ADDRESS_PATTERN = re.compile(
    r'module\.repository\["(?P<slug>[^"]+)"\]\.github_repository\.this'
)


def _parse_repo_import_candidate(match: re.Match[str]) -> tuple[str, str, str] | None:
    """Parse a regex match into (address, slug, repo_name) or None if invalid.

    Parameters
    ----------
    match : re.Match[str]
        A regex match object from _GITHUB_REPO_ADDRESS_PATTERN.

    Returns
    -------
    tuple[str, str, str] | None
        Tuple of (address, slug, repo_name) if valid, None otherwise.

    """
    address = match.group("address").replace('\\"', '"')
    slug_match = _GITHUB_REPO_SLUG_FROM_ADDRESS_PATTERN.search(address)
    if not slug_match:
        return None
    slug = slug_match.group("slug")
    owner, _, repo_name = slug.partition("/")
    if not owner or not repo_name:
        return None
    return (address, slug, repo_name)


def _deduplicate_preserving_order(items: list[_T]) -> list[_T]:
    """Remove duplicate items while preserving first occurrence order."""
    return list(dict.fromkeys(items))


def detect_missing_repo_imports(output: str) -> list[tuple[str, str, str]]:
    """Return list of (resource address, slug, repo_name) for repos that exist.

    This is a best-effort heuristic based on common GitHub provider diagnostics.
    When GitHub returns a "name already exists" error during apply, this
    typically means the repository exists but isn't tracked in state.

    Parameters
    ----------
    output : str
        Combined stdout/stderr output from a tofu command.

    Returns
    -------
    list[tuple[str, str, str]]
        List of tuples containing (resource_address, slug, repo_name) for each
        repository that appears to need importing.

    """
    if not output:
        return []

    if _GITHUB_REPO_EXISTS_MARKER not in output.lower():
        return []

    candidates = [
        candidate
        for match in _GITHUB_REPO_ADDRESS_PATTERN.finditer(output)
        if (candidate := _parse_repo_import_candidate(match)) is not None
    ]

    return _deduplicate_preserving_order(candidates)


def _parse_slugs_from_matches(normalized_output: str) -> list[str]:
    """Extract repository slugs from regex matches in normalized output.

    Parameters
    ----------
    normalized_output : str
        Output string with escaped quotes normalized.

    Returns
    -------
    list[str]
        List of non-empty slugs found in the output.

    """
    candidates: list[str] = []
    for match in _GITHUB_REPO_SLUG_FROM_ADDRESS_PATTERN.finditer(normalized_output):
        slug = match.group("slug").strip()
        if slug:
            candidates.append(slug)
    return candidates


def detect_state_forgets_for_prevent_destroy(output: str) -> list[str]:
    """Return a list of slugs that look like they should be removed from state.

    When a repository is removed from the inventory, OpenTofu plans to destroy
    the corresponding `github_repository` resources. The module enforces
    `prevent_destroy = true`, so the apply fails. For disenrolment, the desired
    outcome is typically to *forget* the resource (remove it from state) while
    leaving the GitHub repository intact.

    Parameters
    ----------
    output : str
        Combined stdout/stderr output from a tofu command.

    Returns
    -------
    list[str]
        List of repository slugs that should be removed from state.

    """
    if not output:
        return []

    lowered = output.lower()
    if not any(marker in lowered for marker in _GITHUB_PREVENT_DESTROY_MARKERS):
        return []

    normalized_output = output.replace('\\"', '"')
    candidates = _parse_slugs_from_matches(normalized_output)
    return _deduplicate_preserving_order(candidates)
