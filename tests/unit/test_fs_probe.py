"""Unit tests for the presence-or-refusal filesystem probe.

`Path.is_file()` answers "does not exist" and "the filesystem refused to say"
with the same `False`. Every rule fact built on it would report an unreadable
checkout as a compliant absence, so the distinction is the whole point of this
module and each case here pins one side of it.

A `stat` plus a regular-file test is not enough either, which is the second
lesson these tests carry. Two shapes are occupied paths that such a probe
reports as empty ones: a dangling symbolic link, and a directory where a file
is expected. Both are states to report, not states to pass over.
"""

from __future__ import annotations

import typing as typ

from concordat.rules.fs_probe import probe_file

if typ.TYPE_CHECKING:
    import pathlib

    import pytest


def test_a_regular_file_is_present(tmp_path: pathlib.Path) -> None:
    """The ordinary case: the file is there and the filesystem said so."""
    target = tmp_path / "config.toml"
    target.write_text("", encoding="utf-8")
    probe = probe_file(target)
    assert probe.present is True, "a regular file is present"
    assert probe.read_error is None, "nothing refused the question"


def test_a_missing_path_is_absent(tmp_path: pathlib.Path) -> None:
    """An absence is an answer, not a failure."""
    probe = probe_file(tmp_path / "nothing")
    assert probe.present is False, "a missing path is not present"
    assert probe.read_error is None, "the filesystem answered the question"


def test_a_path_under_a_file_is_absent(tmp_path: pathlib.Path) -> None:
    """A parent that is not a directory means the file is not there.

    `ENOTDIR`, like `ENOENT`, is the filesystem saying nothing is at that
    path. Those two are the whole of absence.
    """
    parent = tmp_path / "cargo"
    parent.write_text("", encoding="utf-8")
    probe = probe_file(parent / "config.toml")
    assert probe.present is False, "nothing can live beneath a regular file"
    assert probe.read_error is None, "the filesystem answered the question"


def test_a_dangling_symlink_is_a_refusal_not_an_absence(
    tmp_path: pathlib.Path,
) -> None:
    """Something is there, and it names a target that is not.

    `stat` follows links, so a dangling one raises `FileNotFoundError` exactly
    as a missing path does. Reading that as an absence turns a broken checkout
    into a repository that simply never had the file, which is the compliant
    answer rather than the true one.
    """
    link = tmp_path / "config.toml"
    link.symlink_to(tmp_path / "does-not-exist")
    probe = probe_file(link)
    assert probe.present is False, "an unresolved link is not a readable file"
    assert probe.read_error is not None, (
        "a link that does not resolve is a state to report, not an absence"
    )
    assert str(link) in probe.read_error, (
        f"the diagnostic should name the path, got {probe.read_error!r}"
    )
    assert "resolve" in probe.read_error, (
        f"the diagnostic should give the reason, got {probe.read_error!r}"
    )


def test_a_directory_in_the_file_s_place_is_a_refusal(
    tmp_path: pathlib.Path,
) -> None:
    """The path is occupied by something the reader cannot parse.

    The filesystem answered, but it did not answer "nothing is here". A
    directory named `config.toml` is a misconfiguration to surface rather than
    a configuration the repository never wrote.
    """
    target = tmp_path / "config.toml"
    target.mkdir()
    probe = probe_file(target)
    assert probe.present is False, "a directory is not a regular file"
    assert probe.read_error is not None, (
        "an occupied path is a state to report, not an absence"
    )
    assert str(target) in probe.read_error, (
        f"the diagnostic should name the path, got {probe.read_error!r}"
    )
    assert "directory" in probe.read_error, (
        f"the diagnostic should say what occupies it, got {probe.read_error!r}"
    )


def test_a_refusal_is_neither_presence_nor_absence(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The case the probe exists for: `is_file()` would report this absent."""

    def refuse(_self: pathlib.Path, *_args: object, **_kwargs: object) -> object:
        message = "Permission denied"
        raise PermissionError(13, message)

    monkeypatch.setattr("pathlib.Path.stat", refuse)
    probe = probe_file(tmp_path / "config.toml")
    assert probe.present is False, "a refusal proves no presence"
    assert probe.read_error is not None, (
        "a refusal must be distinguishable from an absence"
    )
    assert "Permission denied" in probe.read_error, (
        f"the diagnostic should carry the reason, got {probe.read_error!r}"
    )
