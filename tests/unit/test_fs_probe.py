"""Unit tests for the presence-or-refusal filesystem probe.

`Path.is_file()` answers "does not exist" and "the filesystem refused to say"
with the same `False`. Every rule fact built on it would report an unreadable
checkout as a compliant absence, so the distinction is the whole point of this
module and each case here pins one side of it.
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


def test_a_directory_in_the_file_s_place_is_absent(tmp_path: pathlib.Path) -> None:
    """A directory is not the file, and the filesystem described it fine."""
    target = tmp_path / "config.toml"
    target.mkdir()
    probe = probe_file(target)
    assert probe.present is False, "a directory is not a regular file"
    assert probe.read_error is None, "the filesystem answered the question"


def test_a_path_under_a_file_is_absent(tmp_path: pathlib.Path) -> None:
    """A parent that is not a directory means the file is not there."""
    parent = tmp_path / "cargo"
    parent.write_text("", encoding="utf-8")
    probe = probe_file(parent / "config.toml")
    assert probe.present is False, "nothing can live beneath a regular file"
    assert probe.read_error is None, "the filesystem answered the question"


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
