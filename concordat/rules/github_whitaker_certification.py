"""Answer QG-002 certification questions by asking the GitHub REST API.

The adapter half of `concordat.rules.whitaker_certification`: it reads the
certification tags, the ancestry of a pinned revision and the runtime content of
the action directory from the action repository, and hands them to the
transport-free decision. Every reply a failing API gives becomes a
`CertificationUnavailable`, which the decision turns into an `unknown`
verdict, so an unreachable or rate-limited GitHub is never read as a breach.

Nothing here runs while an envelope is built. The rule-run command composes a
`GithubCertifier` and hands it to the envelope step explicitly, so the checkout
inspection stays a query and the network stays at the command boundary.

What GitHub says about a commit never changes, so a certifier keeps what it
learns for its own lifetime: the tag list, each ancestry answer and each
directory digest are asked once per run, however many pins share them.
"""

from __future__ import annotations

import logging
import typing as typ

import requests

from concordat.auditor.github import (
    GithubClient,
    GithubError,
    GithubRateLimitError,
)

from .whitaker_certification import (
    DEFAULT_BRANCH,
    DEFAULT_TAG_PREFIX,
    Certification,
    CertificationUnavailable,
    Floor,
    NoSuchCommit,
    certify_revision,
    runtime_digest_of,
)

if typ.TYPE_CHECKING:
    import collections.abc as cabc

_logger = logging.getLogger(__name__)

# What the client raises for a reply it could not use: a refusal or other HTTP
# failure, a transport failure, or a body that is not JSON.
_LOOKUP_FAILURES: typ.Final = (GithubError, requests.RequestException, ValueError)
_ON_BRANCH: typ.Final = frozenset({"ahead", "identical"})
_DESCENDS: typ.Final = frozenset({"ahead", "identical"})
_TREE_TYPE: typ.Final = "tree"
_COMMIT_TYPE: typ.Final = "commit"


def _unavailable(endpoint: str, error: Exception) -> CertificationUnavailable:
    """Return the failure for a lookup of *endpoint* that could not be read.

    A spent rate limit is flagged, so the finding can say that authenticating
    or waiting would settle it, rather than suggest the pin is wrong.

    Returns
    -------
    CertificationUnavailable
        The failure, carrying the endpoint and what went wrong.
    """
    if isinstance(error, GithubRateLimitError):
        return CertificationUnavailable(
            f"{endpoint} was rate limited: {error}", rate_limited=True
        )
    return CertificationUnavailable(f"{endpoint} could not be read: {error}")


class GithubCertificationSource:
    """Read certification facts from one action repository.

    The client is built on the first question, so a checkout that pins nothing
    reads no credentials and opens no session. The factory may raise; that is
    a failure to audit at all, and it propagates to the command.
    """

    def __init__(  # one keyword per fact of the action's location.
        self,
        client_factory: cabc.Callable[[], GithubClient],
        repository: str,
        directory: str,
        *,
        tag_prefix: str = DEFAULT_TAG_PREFIX,
        branch: str = DEFAULT_BRANCH,
    ) -> None:
        """Keep the factory and where the action lives (`owner/name`)."""
        self._client_factory = client_factory
        self._owner, _, self._name = repository.partition("/")
        self._directory = directory
        self._tag_prefix = tag_prefix
        self._branch = branch
        self._client: GithubClient | None = None
        self._floors: list[Floor] | None = None
        self._comparisons: dict[tuple[str, str], str | None] = {}
        self._digests: dict[str, str | None] = {}

    def floors(self) -> list[Floor]:
        """Return every certification tag with the commit it points at.

        A listing that fails, or a tag that does not peel to a commit, ends the
        decision as unavailable (see `CertificationUnavailable`).

        Returns
        -------
        list[Floor]
            The tags, sorted by name.
        """
        if self._floors is None:
            self._floors = self._read_floors()
        return self._floors

    def is_on_default_branch(self, sha: str) -> bool:
        """Report whether *sha* is the default branch's tip or one of its ancestors.

        A commit GitHub does not know ends the decision (see `NoSuchCommit`).

        Returns
        -------
        bool
            True when the branch is `ahead` of *sha* or identical to it.
        """
        return self._compare(sha, self._branch) in _ON_BRANCH

    def descends_from(self, floor: str, sha: str) -> bool:
        """Report whether *sha* equals, or descends from, the commit *floor*.

        A commit GitHub does not know ends the decision (see `NoSuchCommit`).

        Returns
        -------
        bool
            True when *sha* is `ahead` of *floor* or identical to it.
        """
        return self._compare(floor, sha) in _DESCENDS

    def runtime_digest(self, sha: str) -> str | None:
        """Return the action directory's runtime digest at *sha*.

        A directory that cannot be read, or a listing GitHub truncates (which
        would digest part of the directory), ends the decision as unavailable.

        Returns
        -------
        str | None
            The digest, or None when the commit has no such directory.
        """
        if sha not in self._digests:
            self._digests[sha] = self._read_digest(sha)
        return self._digests[sha]

    def _compare(self, base: str, head: str) -> str | None:
        """Return how *head* stands to *base*, asked once per pair.

        Returns
        -------
        str
            The comparison status GitHub reported.

        Raises
        ------
        NoSuchCommit
            When either revision is unknown to the repository.
        """
        if (base, head) not in self._comparisons:
            self._comparisons[base, head] = self._ask_compare(base, head)
        status = self._comparisons[base, head]
        if status is None:
            message = f"{base}...{head} names a revision GitHub does not know"
            raise NoSuchCommit(message)
        return status

    def _ask_compare(self, base: str, head: str) -> str | None:
        """Return the comparison status for one pair, or None when unknown."""
        endpoint = f"compare/{base}...{head}"
        try:
            return self._connected().compare_status(self._owner, self._name, base, head)
        except _LOOKUP_FAILURES as error:
            failure = _unavailable(endpoint, error)
            raise failure from error

    def _read_floors(self) -> list[Floor]:
        """Read and peel every certification tag."""
        client = self._connected()
        namespace = f"tags/{self._tag_prefix}"
        try:
            refs = client.matching_refs(self._owner, self._name, namespace)
        except _LOOKUP_FAILURES as error:
            failure = _unavailable(f"git/matching-refs/{namespace}", error)
            raise failure from error
        floors = [self._floor(client, ref) for ref in refs]
        return sorted(floors)

    def _floor(self, client: GithubClient, ref: cabc.Mapping[str, object]) -> Floor:
        """Return the floor one tag ref stands for, peeling an annotated tag.

        Returns
        -------
        Floor
            The tag's name and the commit it points at.

        Raises
        ------
        CertificationUnavailable
            When the tag cannot be read, or does not lead to a commit.
        """
        name = str(ref.get("ref", "")).removeprefix("refs/tags/")
        target = ref.get("object")
        target = target if isinstance(target, dict) else {}
        sha, kind = target.get("sha"), target.get("type")
        if kind == "tag" and isinstance(sha, str):
            inner = self._tag_target(client, sha)
            sha, kind = inner.get("sha"), inner.get("type")
        if kind != _COMMIT_TYPE or not isinstance(sha, str):
            message = f"certification tag {name} does not point at a commit"
            raise CertificationUnavailable(message)
        return Floor(name, sha)

    def _tag_target(self, client: GithubClient, sha: str) -> dict[str, object]:
        """Return the object an annotated tag *sha* points at, or `{}`."""
        try:
            tag = client.git_tag(self._owner, self._name, sha)
        except _LOOKUP_FAILURES as error:
            failure = _unavailable(f"git/tags/{sha}", error)
            raise failure from error
        inner = tag.get("object") if isinstance(tag, dict) else None
        return inner if isinstance(inner, dict) else {}

    def _read_digest(self, sha: str) -> str | None:
        """Read the action directory at *sha* and digest its runtime files."""
        treeish = f"{sha}:{self._directory}"
        try:
            tree = self._connected().git_tree(
                self._owner, self._name, treeish, recursive=True
            )
        except _LOOKUP_FAILURES as error:
            failure = _unavailable(f"git/trees/{treeish}", error)
            raise failure from error
        if tree is None:
            return None
        if tree.get("truncated"):
            message = f"git/trees/{treeish} was truncated by GitHub"
            raise CertificationUnavailable(message)
        entries = tree.get("tree")
        if not isinstance(entries, list):
            message = f"git/trees/{treeish} answered with no tree"
            raise CertificationUnavailable(message)
        return runtime_digest_of(
            (str(entry["path"]), str(entry["mode"]), str(entry["sha"]))
            for entry in entries
            if isinstance(entry, dict) and entry.get("type") != _TREE_TYPE
        )

    def _connected(self) -> GithubClient:
        """Return the client, building it on first use."""
        if self._client is None:
            self._client = self._client_factory()
        return self._client


def github_certifier(
    client_factory: cabc.Callable[[], GithubClient],
    parameters: cabc.Mapping[str, object],
) -> GithubCertifier:
    """Return a certifier for the action the rule's *parameters* name.

    Returns
    -------
    GithubCertifier
        A certifier whose GitHub client is built only on its first question.
    """

    def text(key: str, default: str) -> str:
        """Return the string parameter *key*, or *default* when it is not one."""
        value = parameters.get(key)
        return value if isinstance(value, str) and value else default

    source = GithubCertificationSource(
        client_factory,
        text("action_repository", "leynos/shared-actions"),
        text("action_directory", ".github/actions/install-whitaker"),
        tag_prefix=text("certification_tag_prefix", DEFAULT_TAG_PREFIX),
        branch=text("action_branch", DEFAULT_BRANCH),
    )
    return GithubCertifier(source)


class GithubCertifier:
    """Certify revisions of one action by asking GitHub, one source per run."""

    def __init__(self, source: GithubCertificationSource) -> None:
        """Keep the *source* whose answers every revision shares."""
        self._source = source
        self._verdicts: dict[str, Certification] = {}

    def __call__(self, sha: str) -> Certification:
        """Return whether *sha* is a certified revision, asking once per run.

        The outcome is logged at debug level by category only: the reason can
        carry part of a GitHub response body, so it reaches the finding alone.

        Returns
        -------
        Certification
            The verdict for *sha*.
        """
        if sha not in self._verdicts:
            verdict = certify_revision(self._source, sha)
            _logger.debug("certified install-whitaker@%s: %s", sha, verdict["status"])
            self._verdicts[sha] = verdict
        return self._verdicts[sha]
