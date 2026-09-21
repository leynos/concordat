"""Distinguish an absent file from one the filesystem refused to describe.

`pathlib.Path.is_file()` answers both questions with `False`: a path that does
not exist and a path whose `stat` failed on permissions, a broken mount, or a
filename the filesystem rejects are indistinguishable. Every rule fact built on
that probe would report an unreadable checkout as a compliant absence, which is
the one answer a fail-closed audit must never give by accident.

The probe here separates the two. Callers turn an absence into whatever their
clause means by it, and a refusal into a fail-closed fact carrying the reason.
"""

from __future__ import annotations

import stat
import typing as typ

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    import pathlib


class FileProbe(typ.NamedTuple):
    """The outcome of asking whether one path is a readable regular file.

    ``present`` is true only for a regular file the filesystem described.
    ``read_error`` is set when it refused to describe the path at all, which
    is neither presence nor absence and must not be reported as either.
    """

    present: bool
    read_error: str | None


ABSENT: typ.Final = FileProbe(present=False, read_error=None)


def _probe(
    path: pathlib.Path,
    describes: cabc.Callable[[int], bool],
    *,
    follow_symlinks: bool = True,
) -> FileProbe:
    """Report whether *path* is the kind *describes* accepts.

    Returns
    -------
    FileProbe
        Presence, or the filesystem's diagnostic when the path could not be
        described.
    """
    try:
        info = path.stat() if follow_symlinks else path.lstat()
    except (FileNotFoundError, NotADirectoryError):
        # A missing path, or one whose parent is not a directory: both mean
        # the path is not there, which is an answer rather than a failure.
        return ABSENT
    except OSError as error:
        return FileProbe(present=False, read_error=str(error))
    if not describes(info.st_mode):
        # Something else in the path's place is not the path asked about, and
        # the filesystem answered the question, so it is an absence.
        return ABSENT
    return FileProbe(present=True, read_error=None)


def probe_file(path: pathlib.Path) -> FileProbe:
    """Report whether *path* is a regular file, or why that could not be told.

    Returns
    -------
    FileProbe
        Presence, or the filesystem's diagnostic when the path could not be
        described.
    """
    return _probe(path, stat.S_ISREG)


def probe_dir(path: pathlib.Path) -> FileProbe:
    """Report whether *path* is a directory, or why that could not be told.

    Returns
    -------
    FileProbe
        Presence, or the filesystem's diagnostic when the path could not be
        described.
    """
    return _probe(path, stat.S_ISDIR)


def probe_symlink(path: pathlib.Path) -> FileProbe:
    """Report whether *path* is itself a symbolic link.

    The link is read without being followed, so a link to a missing target is
    still a link.

    Returns
    -------
    FileProbe
        Presence, or the filesystem's diagnostic when the path could not be
        described.
    """
    return _probe(path, stat.S_ISLNK, follow_symlinks=False)


def probe_any(path: pathlib.Path) -> FileProbe:
    """Report whether anything exists at *path*, of whatever kind.

    Returns
    -------
    FileProbe
        Presence, or the filesystem's diagnostic when the path could not be
        described.
    """
    return _probe(path, lambda _mode: True)
