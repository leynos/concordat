"""Property tests for the on-disk action-pin cache's key and round-trip rules.

The repository in a key comes from a workflow the audit does not control and
becomes a path, so the interesting invariants hold over arbitrary text: a key
that is not a plain `owner/name` pair and a forty-digit SHA writes nothing,
and any key that is one round-trips through an independent cache object and
stays inside the cache directory.
"""

from __future__ import annotations

import pathlib
import tempfile
import typing as typ

from hypothesis import given, settings
from hypothesis import strategies as st

from concordat.rules.action_pins import PinResolution, commit_pin, tag_pin
from concordat.rules.pin_cache import PinCache, ReadOutcome

ROOT: typ.Final = "https://api.github.com"
_HEX: typ.Final = "0123456789abcdef"

_SHAS: typ.Final = st.text(alphabet=_HEX, min_size=40, max_size=40)
_SEGMENTS: typ.Final = st.from_regex(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,30}", fullmatch=True)
_REPOSITORIES: typ.Final = st.builds("{}/{}".format, _SEGMENTS, _SEGMENTS)
_ANYTHING: typ.Final = st.text(max_size=60)

# Every example touches the disk, and the suite runs on shared hosts under
# xdist, so a wall-clock deadline would assert the machine's load rather than
# the cache.
_DISK_BOUND: typ.Final = settings(deadline=None)


@st.composite
def _definite_answers(draw: st.DrawFn) -> tuple[str, PinResolution]:
    """Draw a SHA with a definite answer for it, in every shape a resolver gives."""
    sha = draw(_SHAS)
    peeled = draw(st.one_of(st.none(), _SHAS))
    return sha, draw(st.sampled_from([commit_pin(sha), tag_pin(peeled)]))


def _files(directory: pathlib.Path) -> list[pathlib.Path]:
    """Return every file below *directory*, or none if it does not exist."""
    return [path for path in directory.rglob("*") if path.is_file()]


@_DISK_BOUND
@given(_REPOSITORIES, _definite_answers())
def test_any_plain_key_round_trips_through_an_independent_cache(
    repository: str, answer: tuple[str, PinResolution]
) -> None:
    """A written answer is read back unchanged, from a fresh cache object."""
    sha, resolution = answer
    with tempfile.TemporaryDirectory() as tmp:
        PinCache(pathlib.Path(tmp), ROOT).put(repository, sha, resolution)
        read = PinCache(pathlib.Path(tmp), ROOT).get(repository, sha)
    assert (read.outcome, read.resolution) == (ReadOutcome.HIT, resolution)


@_DISK_BOUND
@given(_REPOSITORIES, _definite_answers())
def test_a_written_entry_stays_inside_the_cache_directory(
    repository: str, answer: tuple[str, PinResolution]
) -> None:
    """Whatever the repository text, the only files written are under the cache."""
    sha, resolution = answer
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        cache_dir = root / "cache"
        PinCache(cache_dir, ROOT).put(repository, sha, resolution)
        assert [path for path in _files(root) if cache_dir not in path.parents] == []
        assert len(_files(cache_dir)) == 1


@_DISK_BOUND
@given(_ANYTHING, _ANYTHING)
def test_arbitrary_text_never_escapes_or_raises(repository: str, sha: str) -> None:
    """Any key is a round trip inside the cache, or bypassed and unwritten."""
    resolution = commit_pin(sha)
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        cache_dir = root / "cache"
        cache = PinCache(cache_dir, ROOT)
        cache.put(repository, sha, resolution)
        read = cache.get(repository, sha)
        assert [path for path in _files(root) if cache_dir not in path.parents] == []
        if read.outcome is ReadOutcome.BYPASSED:
            assert _files(cache_dir) == []
        else:
            assert read.resolution == resolution
