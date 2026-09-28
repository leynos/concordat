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

import typing as typ

import requests

from concordat.auditor.github import GithubClient, GithubError

from .action_pins import (
    OBJECT_COMMIT,
    PinResolution,
    commit_pin,
    tag_pin,
    unresolved_pin,
)

if typ.TYPE_CHECKING:
    import collections.abc as cabc

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

    def __call__(self, repository: str, sha: str) -> PinResolution:
        """Return what *sha* names in *repository* (``owner/name``)."""
        owner, _, name = repository.partition("/")
        client = self._connected()
        try:
            commit = client.git_commit(owner, name, sha)
        except _LOOKUP_FAILURES as error:
            return unresolved_pin(f"git/commits/{sha} could not be read: {error}")
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
        return unresolved_pin(f"git/tags/{sha} could not be read: {error}")
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
