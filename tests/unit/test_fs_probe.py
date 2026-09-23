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

import pytest

from concordat.errors import OperationalRuleError
from concordat.rules.fs_probe import (
    probe_any,
    probe_dir,
    probe_file,
    probe_symlink,
    regular_file_exists,
)

if typ.TYPE_CHECKING:
    import pathlib


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


def test_a_refusal_to_describe_the_link_is_not_an_absence(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same rule one level down, where it is easiest to forget.

    Reaching the link check at all means `stat` raised `FileNotFoundError`,
    which a dangling link and a missing path share. If `lstat` then refuses to
    answer, neither has been established, and reporting an absence would
    reintroduce the defect this function exists to fix.
    """

    def missing(_self: pathlib.Path, *_args: object, **_kwargs: object) -> object:
        message = "No such file or directory"
        raise FileNotFoundError(2, message)

    def refuse(_self: pathlib.Path, *_args: object, **_kwargs: object) -> object:
        message = "Permission denied"
        raise PermissionError(13, message)

    monkeypatch.setattr("pathlib.Path.stat", missing)
    monkeypatch.setattr("pathlib.Path.lstat", refuse)
    target = tmp_path / "config.toml"
    probe = probe_file(target)
    assert probe.present is False, "a refusal proves no presence"
    assert probe.read_error is not None, (
        "an lstat refusal is not evidence that the path is empty"
    )
    assert str(target) in probe.read_error, (
        f"the diagnostic should name the path, got {probe.read_error!r}"
    )
    assert "Permission denied" in probe.read_error, (
        f"the diagnostic should carry the reason, got {probe.read_error!r}"
    )


class TestSiblingProbes:
    """The directory, any-entry and link probes share the file probe's rule.

    `probe_dir` and `probe_any` expect something and refuse an occupied or
    dangling path, as `probe_file` does. `probe_symlink` asks what kind of
    entry is there, so another kind is an answer rather than a refusal.
    """

    def test_a_file_where_a_directory_is_expected_is_a_refusal(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A file in a directory's place is an occupied path, not an absence."""
        target = tmp_path / "workflows"
        target.write_text("", encoding="utf-8")
        probe = probe_dir(target)
        assert probe.present is False, "a file is not a directory"
        assert probe.read_error is not None, "an occupied path must be reported"
        assert "regular file where a directory is expected" in probe.read_error, (
            f"the diagnostic should name the occupant, got {probe.read_error!r}"
        )

    def test_a_directory_is_present(self, tmp_path: pathlib.Path) -> None:
        """The ordinary case for the directory probe."""
        probe = probe_dir(tmp_path)
        assert probe.present is True, "a directory is present"
        assert probe.read_error is None, "nothing refused the question"

    def test_any_entry_behind_a_dangling_link_is_a_refusal(
        self, tmp_path: pathlib.Path
    ) -> None:
        """`probe_any` follows the link, and what it reaches is not there."""
        link = tmp_path / "Makefile"
        link.symlink_to(tmp_path / "gone")
        probe = probe_any(link)
        assert probe.present is False, "an unresolved link reaches nothing"
        assert probe.read_error is not None, "a dangling link is not an absence"

    def test_a_dangling_link_is_still_a_link(self, tmp_path: pathlib.Path) -> None:
        """The link probe reads the entry itself, not its target."""
        link = tmp_path / "README.md"
        link.symlink_to(tmp_path / "gone")
        probe = probe_symlink(link)
        assert probe.present is True, "the entry is a link whatever it names"
        assert probe.read_error is None, "nothing refused the question"

    def test_a_regular_file_is_not_a_link(self, tmp_path: pathlib.Path) -> None:
        """Another kind of entry answers the link question; it is no refusal."""
        target = tmp_path / "README.md"
        target.write_text("", encoding="utf-8")
        probe = probe_symlink(target)
        assert probe.present is False, "a regular file is not a link"
        assert probe.read_error is None, "the filesystem answered the question"


class TestRegularFileExists:
    """The raising counterpart, for callers whose boundary is an exception.

    It converts `probe_file`'s three answers into two: an absence is `False`,
    a readable regular file is `True`, and anything else raises. Hardening the
    probe therefore moved two shapes across that boundary, so both are pinned
    here rather than left to be discovered by a caller.
    """

    def test_a_regular_file_exists(self, tmp_path: pathlib.Path) -> None:
        """The ordinary case returns True rather than raising."""
        target = tmp_path / "Cargo.toml"
        target.write_text("", encoding="utf-8")
        assert regular_file_exists(target, operation="probe") is True, (
            "a readable regular file exists"
        )

    def test_an_absent_path_is_reported_false(self, tmp_path: pathlib.Path) -> None:
        """Absence stays the no-applicability fact, not an error."""
        assert (
            regular_file_exists(tmp_path / "Cargo.toml", operation="probe") is False
        ), "a genuinely absent path is the established no-applicability fact"

    @pytest.mark.parametrize(
        ("occupant", "fragment"),
        [
            pytest.param("directory", "directory", id="directory"),
            pytest.param("dangling", "resolve", id="dangling-symlink"),
        ],
    )
    def test_an_occupied_path_raises_rather_than_reporting_absence(
        self, tmp_path: pathlib.Path, occupant: str, fragment: str
    ) -> None:
        """Both shapes used to read as absence, so both are pinned here.

        A checkout whose `Cargo.toml` is a directory or a link into nothing is
        misconfigured, and reporting it as having no Rust surface hides that
        behind a clean no-applicability result.
        """
        target = tmp_path / "Cargo.toml"
        if occupant == "directory":
            target.mkdir()
        else:
            target.symlink_to(tmp_path / "elsewhere.toml")

        with pytest.raises(OperationalRuleError) as excinfo:
            regular_file_exists(target, operation="probe-cargo")

        message = str(excinfo.value)
        assert fragment in message, (
            f"the diagnostic should say what was wrong, got {message!r}"
        )
        assert str(target) in message, (
            f"the diagnostic should name the path, got {message!r}"
        )
        assert message.count(str(target)) == 1, (
            f"the path belongs in the message once, got {message!r}"
        )
        assert excinfo.value.operation == "probe-cargo", (
            "the caller's operation identifier must reach the error"
        )
