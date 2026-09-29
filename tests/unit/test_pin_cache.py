"""Unit tests for the on-disk action-pin cache."""

from __future__ import annotations

import json
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
from concordat.rules.pin_cache import PinCache, cached_resolver, default_directory

if typ.TYPE_CHECKING:
    import collections.abc as cabc

REPOSITORY: typ.Final = "DavidAnson/markdownlint-cli2-action"
COMMIT: typ.Final = "21c1be1b93ad9ed58fa840aacc3f279cde2a72ff"
TAG_OBJECT: typ.Final = "4580e1612f6407034edd6c0e4e316d725920867b"


def _entry(cache_dir: pathlib.Path, sha: str = COMMIT) -> pathlib.Path:
    """Return where the cache keeps *sha* of `REPOSITORY`."""
    return cache_dir / "davidanson" / "markdownlint-cli2-action" / sha


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
    PinCache(cache_dir).put(REPOSITORY, sha, resolution)
    assert PinCache(cache_dir).get(REPOSITORY, sha) == resolution


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
    cache = PinCache(cache_dir)
    cache.put(REPOSITORY, COMMIT, resolution)
    assert cache.get(REPOSITORY, COMMIT) is None
    assert not cache_dir.exists()


def test_the_key_is_case_insensitive_on_the_repository(
    cache_dir: pathlib.Path,
) -> None:
    """GitHub treats `DavidAnson` and `davidanson` as one owner."""
    cache = PinCache(cache_dir)
    cache.put("DavidAnson/Action", COMMIT, commit_pin(COMMIT))
    assert cache.get("davidanson/action", COMMIT) == commit_pin(COMMIT)


@pytest.mark.parametrize(
    "repository",
    ["../escape/repo", "owner/..", "owner/a/b", "owner", "/repo", "owner/.hidden", ""],
)
def test_an_unsafe_repository_is_neither_read_nor_written(
    cache_dir: pathlib.Path, repository: str
) -> None:
    """A repository from a workflow becomes a path segment only when plain."""
    cache = PinCache(cache_dir)
    cache.put(repository, COMMIT, commit_pin(COMMIT))
    assert cache.get(repository, COMMIT) is None
    assert not cache_dir.exists()


def test_a_sha_that_is_not_forty_hex_digits_is_not_a_key(
    cache_dir: pathlib.Path,
) -> None:
    """A floating ref is judged by the policy, and never reaches the cache."""
    cache = PinCache(cache_dir)
    cache.put(REPOSITORY, "../../x", commit_pin(COMMIT))
    assert not cache_dir.exists()


@pytest.mark.parametrize(
    "content",
    [
        pytest.param("", id="empty"),
        pytest.param("{not json", id="truncated"),
        pytest.param("[]", id="not-an-object"),
        pytest.param(
            json.dumps({
                "version": 2,
                "repository": "davidanson/markdownlint-cli2-action",
                "sha": COMMIT,
                "object_type": "commit",
                "commit": COMMIT,
            }),
            id="future-version",
        ),
        pytest.param(
            json.dumps({
                "version": 1,
                "repository": "davidanson/markdownlint-cli2-action",
                "sha": COMMIT,
                "object_type": "blob",
                "commit": None,
            }),
            id="unknown-object-type",
        ),
        pytest.param(
            json.dumps({
                "version": 1,
                "repository": "davidanson/markdownlint-cli2-action",
                "sha": COMMIT,
                "object_type": "commit",
                "commit": TAG_OBJECT,
            }),
            id="commit-reaching-another-commit",
        ),
        pytest.param(
            json.dumps({
                "version": 1,
                "repository": "someone/else",
                "sha": COMMIT,
                "object_type": "commit",
                "commit": COMMIT,
            }),
            id="entry-for-another-key",
        ),
    ],
)
def test_a_corrupt_entry_is_deleted_and_read_as_a_miss(
    cache_dir: pathlib.Path, content: str
) -> None:
    """A bad entry is repaired by the next lookup, not tripped over again."""
    entry = _entry(cache_dir)
    entry.parent.mkdir(parents=True)
    entry.write_text(content, encoding="utf-8")
    assert PinCache(cache_dir).get(REPOSITORY, COMMIT) is None
    assert not entry.exists()


def test_a_corrupt_entry_is_replaced_by_the_next_answer(
    cache_dir: pathlib.Path,
) -> None:
    """Miss, re-query, rewrite: the corrupt file does not stay in the way."""
    entry = _entry(cache_dir)
    entry.parent.mkdir(parents=True)
    entry.write_text("{", encoding="utf-8")
    calls: list[str] = []

    def ask(_repository: str, sha: str) -> PinResolution:
        calls.append(sha)
        return commit_pin(sha)

    resolve = cached_resolver(ask, PinCache(cache_dir))
    assert resolve(REPOSITORY, COMMIT) == commit_pin(COMMIT)
    assert resolve(REPOSITORY, COMMIT) == commit_pin(COMMIT)
    assert calls == [COMMIT]


def test_a_write_leaves_no_temporary_file_and_a_whole_entry(
    cache_dir: pathlib.Path,
) -> None:
    """The entry appears by rename, so a reader never sees half of one."""
    PinCache(cache_dir).put(REPOSITORY, COMMIT, commit_pin(COMMIT))
    siblings = sorted(path.name for path in _entry(cache_dir).parent.iterdir())
    assert siblings == [COMMIT]
    assert json.loads(_entry(cache_dir).read_text(encoding="utf-8"))["version"] == 1


def test_a_failed_write_keeps_the_old_entry_and_the_audit_going(
    cache_dir: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rename that fails leaves no temporary file and does not raise."""
    cache = PinCache(cache_dir)
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

    first = cached_resolver(ask, PinCache(cache_dir))
    second = cached_resolver(ask, PinCache(cache_dir))
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

    resolve = cached_resolver(ask, PinCache(cache_dir))
    assert resolve(REPOSITORY, COMMIT)["object_type"] is None
    assert resolve(REPOSITORY, COMMIT) == commit_pin(COMMIT)
    assert resolve(REPOSITORY, COMMIT) == commit_pin(COMMIT)
    assert calls == [COMMIT, COMMIT]


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
