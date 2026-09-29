"""Keep definite action-pin answers on disk so a sweep asks GitHub once per pin.

What a pinned SHA names in an action repository never changes: a commit stays
a commit and an annotated tag object stays a tag object that peels to the same
commit. An estate sweep audits many repositories that pin the same few
actions, and each run is a fresh process, so the per-run memo in
`GithubPinResolver` cannot spare the second repository a lookup. This module
keeps those answers between runs.

Only definite answers are kept. An unresolved pin carries a transient reason:
a spent rate limit, an outage, a refusal that a new token would lift. Caching
one would freeze that reason past its cause, so `put` refuses it and the next
run asks again.

Entries are keyed by the API root, `owner/repository` and `sha`, and written
atomically, so a run killed mid-write leaves either the old entry or none. A
cache is an optimization, never a source of truth: an entry that cannot be
decoded, or that does not match its own key, is read as a miss and discarded
by the caller, and a cache that cannot be written costs a warning rather than
the audit.
"""

from __future__ import annotations

import contextlib
import dataclasses
import enum
import hashlib
import json
import logging
import os
import pathlib
import re
import tempfile
import typing as typ

from .action_pins import (
    FULL_SHA,
    OBJECT_COMMIT,
    OBJECT_TAG,
    PinResolution,
    commit_pin,
    tag_pin,
)

if typ.TYPE_CHECKING:
    import collections.abc as cabc

    from .action_pins import PinResolver

_logger = logging.getLogger(__name__)

FORMAT_VERSION: typ.Final = 1
DIRECTORY_VARIABLE: typ.Final = "CONCORDAT_PIN_CACHE_DIR"

# The repository comes from a workflow the audit does not control and becomes
# a path segment, so anything but a plain owner/name pair is not cached.
_SEGMENT: typ.Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_DEFINITE: typ.Final = frozenset({OBJECT_COMMIT, OBJECT_TAG})
# Hex digits of the API-root digest kept as a directory name: enough that two
# roots do not collide, short enough to read in a listing.
_ROOT_DIGEST_LENGTH: typ.Final = 16


class ReadOutcome(enum.Enum):
    """What reading one cache key found; the values are safe to log."""

    HIT = "hit"
    MISS = "miss"
    CORRUPT = "corrupt"
    UNREADABLE = "unreadable"
    BYPASSED = "bypassed"


@dataclasses.dataclass(frozen=True, slots=True)
class CacheRead:
    """The result of reading a key: an outcome, and the answer on a hit."""

    outcome: ReadOutcome
    resolution: PinResolution | None = None


def default_directory(environ: cabc.Mapping[str, str]) -> pathlib.Path:
    """Return where the cache lives when the caller does not name a directory.

    `CONCORDAT_PIN_CACHE_DIR` wins, then `$XDG_CACHE_HOME/concordat/action-pins`,
    then `~/.cache/concordat/action-pins`.

    Returns
    -------
    pathlib.Path
        The cache directory, which may not exist yet.

    Examples
    --------
    >>> default_directory({"CONCORDAT_PIN_CACHE_DIR": "/srv/pins"}).as_posix()
    '/srv/pins'
    >>> default_directory({"XDG_CACHE_HOME": "/x"}).as_posix()
    '/x/concordat/action-pins'
    """
    override = environ.get(DIRECTORY_VARIABLE)
    if override:
        return pathlib.Path(override)
    xdg = environ.get("XDG_CACHE_HOME")
    root = pathlib.Path(xdg) if xdg else pathlib.Path.home() / ".cache"
    return root / "concordat" / "action-pins"


def _normalized_root(api_root: str) -> str:
    """Return *api_root* in the form that names one GitHub host's namespace."""
    return api_root.strip().rstrip("/").lower()


class PinCache:
    """Read and write definite pin answers for one GitHub API root.

    Whether a SHA names a commit depends on the host that was asked: the same
    `owner/repository/sha` on github.com and on a GitHub Enterprise Server are
    different questions. Each API root therefore has its own directory, named
    by a digest of the normalized root, and every entry records the root it
    answers for.
    """

    def __init__(self, directory: pathlib.Path, api_root: str) -> None:
        """Keep *directory* and the *api_root* it answers for.

        Nothing is created until the first write.
        """
        self._directory = directory
        self._api_root = _normalized_root(api_root)
        digest = hashlib.sha256(self._api_root.encode("utf-8")).hexdigest()
        self._root_directory = directory / digest[:_ROOT_DIGEST_LENGTH]

    def get(self, repository: str, sha: str) -> CacheRead:
        """Return what the cache holds for *sha* in *repository*, changing nothing.

        A missing, corrupt, unreadable and unsafe key are distinct outcomes,
        so the caller decides whether a corrupt entry is repaired; see
        `discard`.

        Returns
        -------
        CacheRead
            The outcome and, on a hit, the kept answer.
        """
        path = self._path(repository, sha)
        if path is None:
            return CacheRead(ReadOutcome.BYPASSED)
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            return CacheRead(ReadOutcome.MISS)
        except OSError as error:
            _logger.warning("could not read pin cache entry %s: %s", path, error)
            return CacheRead(ReadOutcome.UNREADABLE)
        resolution = _decode(data, self._api_root, repository, sha)
        if resolution is None:
            return CacheRead(ReadOutcome.CORRUPT)
        return CacheRead(ReadOutcome.HIT, resolution)

    def discard(self, repository: str, sha: str) -> None:
        """Delete the entry for the key, if there is one and it can be deleted."""
        path = self._path(repository, sha)
        if path is None:
            return
        _logger.warning("discarding pin cache entry %s", path)
        with contextlib.suppress(OSError):
            path.unlink()

    def put(self, repository: str, sha: str, resolution: PinResolution) -> None:
        """Keep *resolution* unless it is unresolved, writing atomically.

        An unresolved answer, or a repository that is not a plain
        ``owner/name`` pair, is not kept. A failed write is logged and
        dropped: the audit already has its answer.
        """
        path = self._path(repository, sha)
        if path is None or resolution["object_type"] not in _DEFINITE:
            return
        document = _encode(self._api_root, repository, sha, resolution)
        try:
            _write_atomically(path, json.dumps(document, sort_keys=True))
        except OSError as error:
            _logger.warning("could not write pin cache entry %s: %s", path, error)

    def _path(self, repository: str, sha: str) -> pathlib.Path | None:
        """Return the entry path for the key, or ``None`` for an unsafe key."""
        owner, _, name = repository.partition("/")
        segments = (owner, name)
        if not all(_SEGMENT.match(part) for part in segments) or not FULL_SHA.match(
            sha
        ):
            return None
        # GitHub names are case-insensitive, so one action is one directory.
        return self._root_directory.joinpath(*(part.lower() for part in segments), sha)


def cached_resolver(resolver: PinResolver, cache: PinCache) -> PinResolver:
    """Return *resolver* consulting *cache* first and feeding it after.

    A corrupt entry is discarded before the lookup that replaces it. Each
    read's outcome is logged at debug level as a bounded category.

    Returns
    -------
    PinResolver
        A resolver that asks *resolver* only on a miss.

    Examples
    --------
    >>> import tempfile, pathlib
    >>> from concordat.rules.action_pins import commit_pin
    >>> sha = "a" * 40
    >>> calls = []
    >>> def ask(repository, pin):
    ...     calls.append(pin)
    ...     return commit_pin(pin)
    >>> root = "https://api.github.com"
    >>> with tempfile.TemporaryDirectory() as tmp:
    ...     first = cached_resolver(ask, PinCache(pathlib.Path(tmp), root))
    ...     second = cached_resolver(ask, PinCache(pathlib.Path(tmp), root))
    ...     _ = first("o/r", sha), second("o/r", sha)
    >>> len(calls)
    1
    """

    def resolve(repository: str, sha: str) -> PinResolution:
        read = cache.get(repository, sha)
        _logger.debug(
            "read pin cache for %s@%s: %s", repository, sha, read.outcome.value
        )
        if read.resolution is not None:
            return read.resolution
        if read.outcome is ReadOutcome.CORRUPT:
            cache.discard(repository, sha)
        resolution = resolver(repository, sha)
        cache.put(repository, sha, resolution)
        return resolution

    return resolve


def _encode(
    api_root: str, repository: str, sha: str, resolution: PinResolution
) -> dict[str, object]:
    """Return the JSON document for one definite answer."""
    return {
        "version": FORMAT_VERSION,
        "api_root": api_root,
        "repository": repository.lower(),
        "sha": sha,
        "object_type": resolution["object_type"],
        "commit": resolution["commit"],
    }


def _document(data: bytes) -> dict[str, object] | None:
    """Return the JSON object in *data*, or ``None`` if it is anything else.

    Bytes that are not valid UTF-8 are not JSON either, so they are as corrupt
    as a truncated file.

    Returns
    -------
    dict[str, object] | None
        The decoded object, or ``None``.
    """
    try:
        document = json.loads(data)
    except ValueError:
        return None
    return document if isinstance(document, dict) else None


def _matches_key(
    document: cabc.Mapping[str, object], api_root: str, repository: str, sha: str
) -> bool:
    """Say whether *document* is a current-format entry for this exact key."""
    return (
        document.get("version") == FORMAT_VERSION
        and document.get("api_root") == api_root
        and document.get("repository") == repository.lower()
        and document.get("sha") == sha
    )


def _answer(document: cabc.Mapping[str, object], sha: str) -> PinResolution | None:
    """Return the answer *document* records, or ``None`` if none could arise.

    A commit pin reaches itself; a tag object reaches a commit or, when its
    target is not a commit, nothing. Any other shape no resolver would give.

    Returns
    -------
    PinResolution | None
        The recorded answer, or ``None`` for an impossible one.
    """
    kind, commit = document.get("object_type"), document.get("commit")
    if kind == OBJECT_COMMIT:
        return commit_pin(sha) if commit == sha else None
    is_peeled_or_absent = commit is None or (
        isinstance(commit, str) and FULL_SHA.match(commit) is not None
    )
    return tag_pin(commit) if kind == OBJECT_TAG and is_peeled_or_absent else None


def _decode(
    data: bytes, api_root: str, repository: str, sha: str
) -> PinResolution | None:
    """Return the answer *data* records for the key, or ``None`` if it is bad.

    Returns
    -------
    PinResolution | None
        The recorded answer, or ``None`` for an entry that is not the JSON
        object `_encode` writes, names another key, or records an impossible
        answer.
    """
    document = _document(data)
    if document is None or not _matches_key(document, api_root, repository, sha):
        return None
    return _answer(document, sha)


def _write_atomically(path: pathlib.Path, payload: str) -> None:
    """Write *payload* to *path* through a sibling temporary file and a rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        dir=path.parent, prefix=".pin-", suffix=".tmp"
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
        pathlib.Path(temporary).replace(path)
    except BaseException:
        with contextlib.suppress(OSError):
            pathlib.Path(temporary).unlink()
        raise
