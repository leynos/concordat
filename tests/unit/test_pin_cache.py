"""Unit tests for the on-disk action-pin cache."""

from __future__ import annotations

import json
import logging
import pathlib
import typing as typ

import pytest

from concordat.rules import pin_cache
from concordat.rules.action_pins import (
    PinResolution,
    commit_pin,
    tag_pin,
    unresolved_pin,
)
from concordat.rules.pin_cache import (
    PinCache,
    ReadOutcome,
    cached_resolver,
    default_directory,
)

if typ.TYPE_CHECKING:
    import collections.abc as cabc

REPOSITORY: typ.Final = "DavidAnson/markdownlint-cli2-action"
COMMIT: typ.Final = "21c1be1b93ad9ed58fa840aacc3f279cde2a72ff"
TAG_OBJECT: typ.Final = "4580e1612f6407034edd6c0e4e316d725920867b"
ROOT: typ.Final = "https://api.github.com"
ENTERPRISE: typ.Final = "https://ghe.example.com/api/v3"


def _cache(directory: pathlib.Path, root: str = ROOT) -> PinCache:
    """Return a cache over *directory* answering for the API root *root*."""
    return PinCache(directory, root)


def _entry(cache_dir: pathlib.Path, sha: str = COMMIT) -> pathlib.Path:
    """Return the file the cache keeps *sha* of `REPOSITORY` in.

    Each API root has one directory below the cache directory, so the file is
    found by shape rather than by recomputing the root's digest here.

    Returns
    -------
    pathlib.Path
        The entry's path.
    """
    [entry] = cache_dir.glob(f"*/davidanson/markdownlint-cli2-action/{sha}")
    return entry


def _document(**overrides: object) -> str:
    """Return a valid entry for `COMMIT` with *overrides* applied, as JSON."""
    document: dict[str, object] = {
        "version": 1,
        "api_root": ROOT,
        "repository": "davidanson/markdownlint-cli2-action",
        "sha": COMMIT,
        "object_type": "commit",
        "commit": COMMIT,
    }
    return json.dumps({**document, **overrides})


def _corrupt(cache_dir: pathlib.Path, content: str | bytes) -> pathlib.Path:
    """Write a valid entry, then replace its bytes with *content*; return it."""
    _cache(cache_dir).put(REPOSITORY, COMMIT, commit_pin(COMMIT))
    entry = _entry(cache_dir)
    entry.write_bytes(content.encode() if isinstance(content, str) else content)
    return entry


@pytest.fixture
def cache_dir(tmp_path: pathlib.Path) -> pathlib.Path:
    """Return a cache directory that does not exist yet."""
    return tmp_path / "cache"


@pytest.mark.parametrize(
    "resolution",
    [
        pytest.param(commit_pin(COMMIT), id="commit"),
        pytest.param(tag_pin(COMMIT), id="tag-with-peeled-commit"),
        pytest.param(tag_pin(None), id="tag-to-a-non-commit"),
    ],
)
def test_a_definite_answer_is_read_back_unchanged(
    cache_dir: pathlib.Path, resolution: PinResolution
) -> None:
    """Commits and tag objects, peeled or not, survive a round trip."""
    sha = COMMIT if resolution["object_type"] == "commit" else TAG_OBJECT
    _cache(cache_dir).put(REPOSITORY, sha, resolution)
    read = _cache(cache_dir).get(REPOSITORY, sha)
    assert (read.outcome, read.resolution) == (ReadOutcome.HIT, resolution)


@pytest.mark.parametrize(
    "resolution",
    [
        pytest.param(unresolved_pin("git/commits/x was refused"), id="refused"),
        pytest.param(unresolved_pin("limited", rate_limited=True), id="rate-limited"),
    ],
)
def test_an_unresolved_answer_is_never_kept(
    cache_dir: pathlib.Path, resolution: PinResolution
) -> None:
    """A transient failure must not outlive its cause."""
    cache = _cache(cache_dir)
    cache.put(REPOSITORY, COMMIT, resolution)
    assert cache.get(REPOSITORY, COMMIT).outcome is ReadOutcome.MISS
    assert not cache_dir.exists()


def test_the_key_is_case_insensitive_on_the_repository(
    cache_dir: pathlib.Path,
) -> None:
    """GitHub treats `DavidAnson` and `davidanson` as one owner."""
    cache = _cache(cache_dir)
    cache.put("DavidAnson/Action", COMMIT, commit_pin(COMMIT))
    assert cache.get("davidanson/action", COMMIT).resolution == commit_pin(COMMIT)


def test_an_answer_does_not_cross_api_roots(cache_dir: pathlib.Path) -> None:
    """The same slug and SHA on another host is another question."""
    _cache(cache_dir, ROOT).put(REPOSITORY, COMMIT, commit_pin(COMMIT))
    other = _cache(cache_dir, ENTERPRISE)
    assert other.get(REPOSITORY, COMMIT).outcome is ReadOutcome.MISS
    other.put(REPOSITORY, COMMIT, tag_pin(None))
    assert _cache(cache_dir, ROOT).get(REPOSITORY, COMMIT).resolution == commit_pin(
        COMMIT
    )


def test_an_api_root_is_normalized(cache_dir: pathlib.Path) -> None:
    """A trailing slash or a different case names the same host."""
    _cache(cache_dir, ROOT).put(REPOSITORY, COMMIT, commit_pin(COMMIT))
    same = _cache(cache_dir, "HTTPS://API.github.com/")
    assert same.get(REPOSITORY, COMMIT).outcome is ReadOutcome.HIT


@pytest.mark.parametrize(
    "repository",
    ["../escape/repo", "owner/..", "owner/a/b", "owner", "/repo", "owner/.hidden", ""],
)
def test_an_unsafe_repository_is_neither_read_nor_written(
    cache_dir: pathlib.Path, repository: str
) -> None:
    """A repository from a workflow becomes a path segment only when plain."""
    cache = _cache(cache_dir)
    cache.put(repository, COMMIT, commit_pin(COMMIT))
    assert cache.get(repository, COMMIT).outcome is ReadOutcome.BYPASSED
    assert not cache_dir.exists()


def test_a_sha_that_is_not_forty_hex_digits_is_not_a_key(
    cache_dir: pathlib.Path,
) -> None:
    """A floating ref is judged by the policy, and never reaches the cache."""
    cache = _cache(cache_dir)
    cache.put(REPOSITORY, "../../x", commit_pin(COMMIT))
    assert cache.get(REPOSITORY, "../../x").outcome is ReadOutcome.BYPASSED
    assert not cache_dir.exists()


@pytest.mark.parametrize(
    "content",
    [
        pytest.param("", id="empty"),
        pytest.param("{not json", id="truncated"),
        pytest.param("[]", id="not-an-object"),
        pytest.param(b"\xff\xfe\x00{", id="not-utf-8"),
        pytest.param(_document(version=2), id="future-version"),
        pytest.param(_document(object_type="blob", commit=None), id="unknown-type"),
        pytest.param(_document(commit=TAG_OBJECT), id="commit-to-another-commit"),
        pytest.param(_document(repository="someone/else"), id="another-repository"),
        pytest.param(_document(api_root=ENTERPRISE), id="another-api-root"),
        pytest.param(_document(sha=TAG_OBJECT), id="another-sha"),
    ],
)
def test_a_corrupt_entry_reads_as_corrupt_and_reading_changes_nothing(
    cache_dir: pathlib.Path, content: str | bytes
) -> None:
    """Reading is a query: a bad entry is reported, not deleted."""
    entry = _corrupt(cache_dir, content)
    assert _cache(cache_dir).get(REPOSITORY, COMMIT).outcome is ReadOutcome.CORRUPT
    assert entry.exists()


def test_discarding_removes_an_entry_and_tolerates_none(
    cache_dir: pathlib.Path,
) -> None:
    """The delete is its own command, and deleting nothing is not an error."""
    entry = _corrupt(cache_dir, "{")
    cache = _cache(cache_dir)
    cache.discard(REPOSITORY, COMMIT)
    cache.discard(REPOSITORY, COMMIT)
    assert not entry.exists()


def test_an_unreadable_entry_is_reported_without_raising(
    cache_dir: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A read that fails for a reason other than absence is a miss, not a crash."""
    _cache(cache_dir).put(REPOSITORY, COMMIT, commit_pin(COMMIT))

    def refuse(_self: pathlib.Path) -> typ.NoReturn:
        raise PermissionError

    monkeypatch.setattr(pathlib.Path, "read_bytes", refuse)
    assert _cache(cache_dir).get(REPOSITORY, COMMIT).outcome is ReadOutcome.UNREADABLE


def test_a_corrupt_entry_is_replaced_by_the_next_answer(
    cache_dir: pathlib.Path,
) -> None:
    """Miss, discard, re-query, rewrite: the corrupt file does not stay."""
    _corrupt(cache_dir, b"\xff")
    calls: list[str] = []

    def ask(_repository: str, sha: str) -> PinResolution:
        calls.append(sha)
        return commit_pin(sha)

    resolve = cached_resolver(ask, _cache(cache_dir))
    assert resolve(REPOSITORY, COMMIT) == commit_pin(COMMIT)
    assert resolve(REPOSITORY, COMMIT) == commit_pin(COMMIT)
    assert calls == [COMMIT]


def test_a_write_leaves_no_temporary_file_and_a_whole_entry(
    cache_dir: pathlib.Path,
) -> None:
    """The entry appears by rename, so a reader never sees half of one."""
    _cache(cache_dir).put(REPOSITORY, COMMIT, commit_pin(COMMIT))
    siblings = sorted(path.name for path in _entry(cache_dir).parent.iterdir())
    assert siblings == [COMMIT]
    assert json.loads(_entry(cache_dir).read_text(encoding="utf-8"))["version"] == 1


def test_a_failed_write_keeps_the_old_entry_and_the_audit_going(
    cache_dir: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rename that fails leaves no temporary file and does not raise."""
    cache = _cache(cache_dir)
    cache.put(REPOSITORY, COMMIT, tag_pin(None))
    before = _entry(cache_dir).read_text(encoding="utf-8")

    def refuse(_self: pathlib.Path, _target: object) -> typ.NoReturn:
        raise PermissionError

    monkeypatch.setattr(pathlib.Path, "replace", refuse)
    cache.put(REPOSITORY, COMMIT, commit_pin(COMMIT))
    monkeypatch.undo()
    assert _entry(cache_dir).read_text(encoding="utf-8") == before
    assert sorted(path.name for path in _entry(cache_dir).parent.iterdir()) == [COMMIT]


def test_the_resolver_asks_once_per_pin_across_caches_on_one_directory(
    cache_dir: pathlib.Path,
) -> None:
    """A second process, modelled by a second cache object, asks nothing."""
    calls: list[tuple[str, str]] = []

    def ask(repository: str, sha: str) -> PinResolution:
        calls.append((repository, sha))
        return commit_pin(sha)

    first = cached_resolver(ask, _cache(cache_dir))
    second = cached_resolver(ask, _cache(cache_dir))
    assert first(REPOSITORY, COMMIT) == second(REPOSITORY, COMMIT)
    assert calls == [(REPOSITORY, COMMIT)]


def test_the_resolver_asks_again_after_an_unresolved_answer(
    cache_dir: pathlib.Path,
) -> None:
    """A refusal is passed through and not remembered."""
    answers: cabc.Iterator[PinResolution] = iter([
        unresolved_pin("refused"),
        commit_pin(COMMIT),
    ])
    calls: list[str] = []

    def ask(_repository: str, sha: str) -> PinResolution:
        calls.append(sha)
        return next(answers)

    resolve = cached_resolver(ask, _cache(cache_dir))
    assert resolve(REPOSITORY, COMMIT)["object_type"] is None
    assert resolve(REPOSITORY, COMMIT) == commit_pin(COMMIT)
    assert resolve(REPOSITORY, COMMIT) == commit_pin(COMMIT)
    assert calls == [COMMIT, COMMIT]


def test_each_read_logs_a_bounded_outcome(
    cache_dir: pathlib.Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A miss then a hit are logged by category, never by entry content."""
    resolve = cached_resolver(lambda _r, sha: commit_pin(sha), _cache(cache_dir))
    with caplog.at_level(logging.DEBUG, logger=pin_cache.__name__):
        resolve(REPOSITORY, COMMIT)
        resolve(REPOSITORY, COMMIT)
    outcomes = [record.getMessage().rsplit(": ", 1)[1] for record in caplog.records]
    assert outcomes == ["miss", "hit"]


@pytest.mark.parametrize(
    ("environ", "expected"),
    [
        pytest.param(
            {pin_cache.DIRECTORY_VARIABLE: "/srv/pins", "XDG_CACHE_HOME": "/x"},
            "/srv/pins",
            id="override-wins",
        ),
        pytest.param({"XDG_CACHE_HOME": "/x"}, "/x/concordat/action-pins", id="xdg"),
        pytest.param({pin_cache.DIRECTORY_VARIABLE: ""}, None, id="empty-override"),
        pytest.param({}, None, id="home-fallback"),
    ],
)
def test_the_default_directory_follows_the_override_then_xdg_then_home(
    environ: dict[str, str], expected: str | None
) -> None:
    """The location is injected as a mapping, never read from the process."""
    resolved = default_directory(environ)
    if expected is None:
        expected = str(pathlib.Path.home() / ".cache/concordat/action-pins")
    assert resolved.as_posix() == expected


def test_a_corrupt_entry_is_discarded_even_when_the_lookup_that_follows_fails(
    cache_dir: pathlib.Path,
) -> None:
    """An unresolved answer cannot overwrite the bad entry, so it is deleted."""
    entry = _corrupt(cache_dir, "{")
    resolve = cached_resolver(
        lambda _r, _s: unresolved_pin("refused"), _cache(cache_dir)
    )
    assert resolve(REPOSITORY, COMMIT)["object_type"] is None
    assert not entry.exists()
