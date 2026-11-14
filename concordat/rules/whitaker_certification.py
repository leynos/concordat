"""Certify shared-actions revisions of `install-whitaker` for QG-002.

The estate provisions Whitaker through one action, and the rule must know that
a pinned revision of it carries the install rules. The audited checkout holds
no shared-actions history, so the question cannot be answered from the
envelope's own facts. It is answered once, at the command boundary, and
recorded in the envelope for the policy to judge.

A revision is *certified* when all of these hold:

* it is on the action repository's default branch (a commit that exists only
  in a fork is not);
* it equals, or descends from, a *floor*: the commit an annotated tag named
  `certified/install-whitaker/<installer version>` points at, created by a
  maintainer after reviewing the action there;
* its *runtime digest* equals that floor's, so a later change to what the
  action runs is not certified until a maintainer tags it.

Runtime content is every file of the action's directory except the `tests/`
tree and Markdown files. The digest is the SHA-256 of the sorted
`<mode> <blob id> <path>` records of those files; blob ids hash content, so it
is a content digest, and a rename, mode change or added file moves it.

This module is the transport-free half: the verdict type, the source protocol
and the decision itself. `concordat.rules.github_whitaker_certification` reads
the facts from GitHub. The decision never raises for an ordinary failure: a
source that cannot answer yields an `unknown` verdict, which the policy
reports as indeterminate rather than as a pass or a breach.
"""

from __future__ import annotations

import hashlib
import typing as typ

if typ.TYPE_CHECKING:
    import collections.abc as cabc

STATUS_CERTIFIED: typ.Final = "certified"
STATUS_UNCERTIFIED: typ.Final = "uncertified"
STATUS_UNKNOWN: typ.Final = "unknown"

EXCLUDED_DIRECTORY: typ.Final = "tests"
EXCLUDED_SUFFIX: typ.Final = ".md"
#: Git modes of a symlink and of a submodule's gitlink.
UNSAFE_MODES: typ.Final = frozenset({"120000", "160000"})

DEFAULT_TAG_PREFIX: typ.Final = "certified/install-whitaker/"
DEFAULT_BRANCH: typ.Final = "main"


class Certification(typ.TypedDict):
    """The verdict on one pinned revision.

    ``status`` is `certified`, `uncertified` (the revision was read and is not
    certified, or does not exist) or `unknown` (the facts could not be read).
    ``floor`` names the tag that certifies a certified revision. ``reason``
    says why a revision is not certified, or why it is unknown.
    """

    status: str
    floor: str | None
    reason: str | None


class Floor(typ.NamedTuple):
    """One certification tag: its name and the commit it points at."""

    tag: str
    commit: str


class NoSuchCommit(LookupError):  # noqa: N818  # a lookup miss, not a failure.
    """Raised by a source when a revision names no commit of the repository."""


class UnsafeContent(ValueError):  # noqa: N818  # a verdict about content, not a bug.
    """Raised by a source for an action directory the digest cannot vouch for.

    A symlink or a submodule in the directory leads to content the digest does
    not read: it hashes the link text or the gitlink commit, so the target can
    change without moving the digest. Such a revision is never certified.
    """


class CertificationUnavailable(RuntimeError):  # noqa: N818  # a source's "no answer", not a bug.
    """Raised by a source that could not read the facts it was asked for.

    ``rate_limited`` says the cause was a spent API rate limit, whose remedy
    (authenticate, or wait) differs from an unreachable or refusing API.
    """

    def __init__(self, reason: str, *, rate_limited: bool = False) -> None:
        """Keep the *reason* and whether a rate limit caused it."""
        super().__init__(reason)
        self.reason = reason
        self.rate_limited = rate_limited


class CertificationSource(typ.Protocol):
    """The facts about shared-actions that certification needs."""

    def floors(self) -> cabc.Sequence[Floor]:
        """Return every certification tag, sorted by name."""
        ...

    def is_on_default_branch(self, sha: str) -> bool:
        """Report whether *sha* is an ancestor of, or is, the default branch tip."""
        ...

    def descends_from(self, floor: str, sha: str) -> bool:
        """Report whether *sha* equals, or descends from, the commit *floor*."""
        ...

    def runtime_digest(self, sha: str) -> str | None:
        """Return the action directory's runtime digest at *sha*, or None."""
        ...


type Certifier = cabc.Callable[[str], Certification]


def is_excluded(relative: str) -> bool:
    """Report whether a path inside the action directory is not runtime content.

    Returns
    -------
    bool
        True for anything under the `tests/` tree and for Markdown files.
    """
    return relative.split("/", 1)[0] == EXCLUDED_DIRECTORY or relative.endswith(
        EXCLUDED_SUFFIX
    )


def unsafe_entry(entries: cabc.Iterable[tuple[str, str, str]]) -> str | None:
    """Return the first path in *entries* that is a symlink or a submodule.

    Every entry is checked, tests and Markdown included: the digest ignores
    those paths, and a link at one of them could lead anywhere.

    Returns
    -------
    str | None
        The path of the first symlink or gitlink, or None when there is none.
    """
    return next((path for path, mode, _blob in entries if mode in UNSAFE_MODES), None)


def runtime_digest_of(entries: cabc.Iterable[tuple[str, str, str]]) -> str:
    """Return the runtime digest of the runtime files among *entries*.

    Each entry is `(path, octal mode, blob id)`, the path relative to the
    action directory. Excluded paths are skipped here, so a caller may pass
    every file of the directory.

    Returns
    -------
    str
        The SHA-256, in hex, of the sorted `<mode> <blob id> <path>` records.
    """
    records = sorted(
        f"{mode} {blob} {path}\n"
        for path, mode, blob in entries
        if not is_excluded(path)
    )
    return hashlib.sha256("".join(records).encode()).hexdigest()


def certified(floor: str) -> Certification:
    """Return the verdict for a revision that *floor* certifies."""
    return Certification(status=STATUS_CERTIFIED, floor=floor, reason=None)


def uncertified(reason: str) -> Certification:
    """Return the verdict for a revision that is read and not certified."""
    return Certification(status=STATUS_UNCERTIFIED, floor=None, reason=reason)


def unknown(reason: str) -> Certification:
    """Return the verdict for a revision whose facts could not be read."""
    return Certification(status=STATUS_UNKNOWN, floor=None, reason=reason)


def certify_revision(source: CertificationSource, sha: str) -> Certification:
    """Decide whether *sha* is a certified revision of the action.

    The cheapest refusals come first, so a revision off the default branch
    costs one lookup. A source failure ends the decision as `unknown`, never as
    a refusal: an API that cannot answer says nothing about the revision.

    Returns
    -------
    Certification
        `certified` with the floor that certifies it, `uncertified` with the
        reason, or `unknown` with the reason the facts could not be read.
    """
    try:
        return _decide(source, sha)
    except NoSuchCommit:
        return uncertified(f"{sha} names no commit of the action repository")
    except UnsafeContent as error:
        return uncertified(str(error))
    except CertificationUnavailable as error:
        return unknown(error.reason)


def _decide(source: CertificationSource, sha: str) -> Certification:
    """Apply the three conditions; sources may raise to end the decision.

    Returns
    -------
    Certification
        The verdict for *sha*.
    """
    if not source.is_on_default_branch(sha):
        return uncertified(f"{sha} is not on the default branch of the action")
    floors = source.floors()
    if not floors:
        return uncertified("the action repository has no certification tags")
    descended = [floor for floor in floors if source.descends_from(floor.commit, sha)]
    if not descended:
        return uncertified(
            f"{sha} is older than, or unrelated to, every certification tag"
        )
    return _match_content(source, sha, descended)


def _floor_digest(source: CertificationSource, floor: Floor) -> str | None:
    """Return a floor's runtime digest, or None for a floor that is unsafe.

    An unsafe floor certifies nothing, but it must not end the comparison: a
    later, safe floor may still match the candidate.

    Returns
    -------
    str | None
        The digest, or None when the floor has no directory or an unsafe one.
    """
    try:
        return source.runtime_digest(floor.commit)
    except UnsafeContent:
        return None


def _match_content(
    source: CertificationSource, sha: str, descended: cabc.Sequence[Floor]
) -> Certification:
    """Certify *sha* when its runtime digest equals that of a floor it follows.

    Returns
    -------
    Certification
        `certified` by the first floor with the same digest, otherwise
        `uncertified` with the reason.
    """
    digest = source.runtime_digest(sha)
    if digest is None:
        return uncertified(f"{sha} has no install-whitaker directory")
    for floor in descended:
        if _floor_digest(source, floor) == digest:
            return certified(floor.tag)
    names = ", ".join(floor.tag for floor in descended)
    return uncertified(
        f"{sha} changes the action's runtime content since {names}; "
        "a maintainer certifies new content by tagging it"
    )
