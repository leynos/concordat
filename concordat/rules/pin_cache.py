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

Entries are keyed by `owner/repository/sha` and written atomically, so a run
killed mid-write leaves either the old entry or none. A cache is an
optimisation, never a source of truth: an entry that cannot be decoded, or
that does not match its own key, is deleted and treated as a miss, and a
cache that cannot be written costs a warning rather than the audit.
"""

from __future__ import annotations

import contextlib
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


class PinCache:
    """Read and write definite pin answers under one directory."""

    def __init__(self, directory: pathlib.Path) -> None:
        """Keep *directory*; nothing is created until the first write."""
        self._directory = directory

    def get(self, repository: str, sha: str) -> PinResolution | None:
        """Return the kept answer for *sha* in *repository*, or ``None``.

        A corrupt entry is deleted and reported as a miss, so the next lookup
        repairs it rather than tripping over it again.

        Returns
        -------
        PinResolution | None
            The kept answer, or ``None`` on a miss or a discarded entry.
        """
        path = self._path(repository, sha)
        if path is None:
            return None
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError as error:
            _logger.warning("could not read pin cache entry %s: %s", path, error)
            return None
        resolution = _decode(text, repository, sha)
        if resolution is None:
            _logger.warning("discarded corrupt pin cache entry %s", path)
            with contextlib.suppress(OSError):
                path.unlink()
        return resolution

    def put(self, repository: str, sha: str, resolution: PinResolution) -> None:
        """Keep *resolution* unless it is unresolved, writing atomically.

        An unresolved answer, or a repository that is not a plain
        ``owner/name`` pair, is not kept. A failed write is logged and
        dropped: the audit already has its answer.
        """
        path = self._path(repository, sha)
        if path is None or resolution["object_type"] not in _DEFINITE:
            return
        payload = json.dumps(_encode(repository, sha, resolution), sort_keys=True)
        try:
            _write_atomically(path, payload)
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
        return self._directory.joinpath(*(part.lower() for part in segments), sha)


def cached_resolver(resolver: PinResolver, cache: PinCache) -> PinResolver:
    """Return *resolver* consulting *cache* first and feeding it after.

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
    >>> with tempfile.TemporaryDirectory() as tmp:
    ...     first = cached_resolver(ask, PinCache(pathlib.Path(tmp)))
    ...     second = cached_resolver(ask, PinCache(pathlib.Path(tmp)))
    ...     _ = first("o/r", sha), second("o/r", sha)
    >>> len(calls)
    1
    """

    def resolve(repository: str, sha: str) -> PinResolution:
        known = cache.get(repository, sha)
        if known is not None:
            return known
        resolution = resolver(repository, sha)
        cache.put(repository, sha, resolution)
        return resolution

    return resolve


def _encode(repository: str, sha: str, resolution: PinResolution) -> dict[str, object]:
    """Return the JSON document for one definite answer."""
    return {
        "version": FORMAT_VERSION,
        "repository": repository.lower(),
        "sha": sha,
        "object_type": resolution["object_type"],
        "commit": resolution["commit"],
    }


def _decode(text: str, repository: str, sha: str) -> PinResolution | None:
    """Return the answer *text* records for the key, or ``None`` if it is bad.

    An entry is bad when it is not the JSON object `_encode` writes, names a
    different key than the file it sits in, or describes an answer no
    resolver would give (a commit pin that reaches another commit, or an
    object type other than a commit or a tag).

    Returns
    -------
    PinResolution | None
        The recorded answer, or ``None`` for a bad entry.
    """
    try:
        document = json.loads(text)
    except ValueError:
        return None
    if not isinstance(document, dict) or not _matches_key(document, repository, sha):
        return None
    commit = document.get("commit")
    if document.get("object_type") == OBJECT_COMMIT:
        return commit_pin(sha) if commit == sha else None
    if document.get("object_type") == OBJECT_TAG and (
        commit is None or (isinstance(commit, str) and FULL_SHA.match(commit))
    ):
        return tag_pin(commit)
    return None


def _matches_key(
    document: cabc.Mapping[str, object], repository: str, sha: str
) -> bool:
    """Say whether *document* is a current-format entry for this exact key."""
    return (
        document.get("version") == FORMAT_VERSION
        and document.get("repository") == repository.lower()
        and document.get("sha") == sha
    )


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
