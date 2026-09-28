"""Resolve action pins against the GitHub REST API.

The adapter half of `concordat.rules.action_pins`: it asks the action
repository's object store what a pinned SHA names, through the shared
`GithubClient`, and translates every reply into a `PinResolution`. The commit
endpoint is asked first; only when it does not know the SHA is the tag
endpoint asked, and a tag is peeled to the commit it points at.

Nothing here runs while an envelope is built. The rule-run command composes a
`GithubPinResolver` and hands it to the envelope builder explicitly, so the
checkout inspection stays a query and the network stays at the command
boundary.
"""

from __future__ import annotations

import logging
import time
import typing as typ

import requests

from concordat.auditor.github import GithubClient, GithubError, GithubRateLimitError

from .action_pins import (
    OBJECT_COMMIT,
    PinResolution,
    commit_pin,
    tag_pin,
    unresolved_pin,
)

if typ.TYPE_CHECKING:
    import collections.abc as cabc

_logger = logging.getLogger(__name__)

# What the client raises for a reply it could not use: a refusal or other
# HTTP failure, a transport failure, or a body that is not JSON.
_LOOKUP_FAILURES: typ.Final = (GithubError, requests.RequestException, ValueError)


class GithubPinResolver:
    """Answer what a pinned SHA names by asking GitHub, one client per instance.

    The client is built on the first lookup, so a rule run whose checkout pins
    nothing reads no credentials and opens no session. The factory may raise;
    that is a failure to audit at all, and it propagates to the command.
    """

    def __init__(self, client_factory: cabc.Callable[[], GithubClient]) -> None:
        """Keep the factory that builds the client on first use."""
        self._client_factory = client_factory
        self._client: GithubClient | None = None
        self._answers: dict[tuple[str, str], PinResolution] = {}

    def __call__(self, repository: str, sha: str) -> PinResolution:
        """Return what *sha* names in *repository* (``owner/name``).

        Each lookup is logged at debug level with its outcome and duration,
        so a slow or refused API can be told apart from an unknown pin. An
        answer is kept for the life of the resolver, so a pin repeated within
        one run costs one lookup, and a spent rate limit is not hammered.

        Returns
        -------
        PinResolution
            A commit, a tag object with its peeled commit, or an unresolved
            pin carrying the reason.
        """
        known = self._answers.get((repository, sha))
        if known is not None:
            return known
        started = time.perf_counter()
        resolution = self._lookup(repository, sha)
        self._answers[repository, sha] = resolution
        _logger.debug(
            "resolved action pin %s@%s: %s in %.3fs",
            repository,
            sha,
            resolution["object_type"] or f"unresolved ({resolution['error']})",
            time.perf_counter() - started,
        )
        return resolution

    def _lookup(self, repository: str, sha: str) -> PinResolution:
        """Ask the commit endpoint, then the tag endpoint only if it must."""
        owner, _, name = repository.partition("/")
        client = self._connected()
        try:
            commit = client.git_commit(owner, name, sha)
        except _LOOKUP_FAILURES as error:
            return _failure(f"git/commits/{sha}", error)
        if commit is not None:
            return commit_pin(sha)
        return _tag_resolution(client, owner, name, sha)

    def _connected(self) -> GithubClient:
        """Return the client, building it on first use."""
        if self._client is None:
            self._client = self._client_factory()
        return self._client


def _tag_resolution(
    client: GithubClient, owner: str, name: str, sha: str
) -> PinResolution:
    """Ask the tag endpoint what *sha* names, peeling a tag to its commit."""
    try:
        tag = client.git_tag(owner, name, sha)
    except _LOOKUP_FAILURES as error:
        return _failure(f"git/tags/{sha}", error)
    if tag is None:
        return unresolved_pin(f"{sha} is neither a commit nor a tag in {owner}/{name}")
    if not isinstance(tag, dict):
        return unresolved_pin(
            f"git/tags/{sha} answered with a body that is not an object"
        )
    return tag_pin(_peeled_commit(tag))


def _peeled_commit(tag: cabc.Mapping[str, object]) -> str | None:
    """Return the commit an annotated tag points at, if it points at one."""
    target = tag.get("object")
    if not isinstance(target, dict) or target.get("type") != OBJECT_COMMIT:
        return None
    sha = target.get("sha")
    return sha if isinstance(sha, str) else None


def _failure(endpoint: str, error: Exception) -> PinResolution:
    """Return the unresolved pin for a lookup of *endpoint* that failed.

    A spent rate limit is flagged, so the policy can say that authenticating
    or waiting would resolve the pin, rather than suggest the pin is wrong.

    Returns
    -------
    PinResolution
        An unresolved pin carrying the endpoint and the failure.
    """
    if isinstance(error, GithubRateLimitError):
        return unresolved_pin(
            f"{endpoint} was rate limited: {error}", rate_limited=True
        )
    return unresolved_pin(f"{endpoint} could not be read: {error}")
