"""Distinguish an absent file from one the filesystem refused to describe.

`pathlib.Path.is_file()` answers both questions with `False`: a path that does
not exist and a path whose `stat` failed on permissions, a broken mount, or a
filename the filesystem rejects are indistinguishable. Every rule fact built on
that probe would report an unreadable checkout as a compliant absence, which is
the one answer a fail-closed audit must never give by accident.

The probe here separates the two. Callers turn an absence into whatever their
clause means by it, and a refusal into a fail-closed fact carrying the reason.

Two shapes are offered over one probe. `probe_file` returns the outcome as a
fact, for callers that carry the reason into a policy clause; `probe_dir` and
`probe_any` ask the same question of a directory and of any entry, and
`probe_symlink` asks what kind of entry is there without following it.
`regular_file_exists` raises instead, for callers whose boundary is an
operational error rather than a fact, such as the applicability evidence in
`concordat.rules.envelope`. Neither reports an unreadable path as an absent
one, and which failures count as absence is decided here, once.
"""

from __future__ import annotations

import stat
import typing as typ

from concordat.errors import OperationalRuleError

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    import pathlib


class FileProbe(typ.NamedTuple):
    """The outcome of asking whether one path is a readable regular file.

    ``present`` is true only for a regular file the filesystem described.
    ``read_error`` is set when it refused to describe the path at all, which
    is neither presence nor absence and must not be reported as either.

    Which field a caller reads depends on the question it is asking, and the
    two questions are easy to confuse. ``present`` answers "is this a readable
    regular file", so a directory where a file is expected is not present and
    carries a reason. A caller that only needs to know whether *anything* is
    there — typically one whose own read has already failed with a missing-file
    error, and which must decide whether that meant absence — keys on
    ``read_error is None`` instead: that is true for a genuine absence alone,
    and false for a dangling link, an occupied path, or a refusal. Reading
    ``present`` for that question reports an existing directory as though
    nothing were there. Noted by jm-concordat-176, whose workflow-directory
    reader asks the second question.
    """

    present: bool
    read_error: str | None


ABSENT: typ.Final = FileProbe(present=False, read_error=None)


def probe_file(path: pathlib.Path) -> FileProbe:
    """Report whether *path* is a regular file, or why that could not be told.

    Absence means the filesystem answered that nothing is there: `ENOENT` with
    no link behind it, or `ENOTDIR` because a component of the path is not a
    directory. Everything else is a refusal, including two shapes that a bare
    `stat` plus a regular-file test reports as absences:

    - a dangling symbolic link, where `lstat` succeeds and `stat` does not.
      Something *is* there, and it names a target that is not; reading that as
      "no configuration" turns a broken checkout into a compliant one.
    - a directory, or any other non-regular file, where a file is expected.
      The path is occupied by something the reader cannot parse, which is a
      state to report rather than to pass over.

    Returns
    -------
    FileProbe
        Presence, or the reason the path could not be read as a file.
    """
    return _probe(path, stat.S_ISREG, "a file")


def probe_dir(path: pathlib.Path) -> FileProbe:
    """Report whether *path* is a directory, or why that could not be told.

    The same rule as `probe_file`, for a reader that expects a directory: a
    regular file or a dangling link in its place is a refusal naming the
    occupant, not an absence.

    Returns
    -------
    FileProbe
        Presence, or the reason the path could not be read as a directory.
    """
    return _probe(path, stat.S_ISDIR, "a directory")


def probe_any(path: pathlib.Path) -> FileProbe:
    """Report whether anything exists at *path*, following links.

    Any kind is accepted, so the only refusals are the filesystem's own and a
    dangling link, whose target is the thing a reader would reach.

    Returns
    -------
    FileProbe
        Presence, or the reason the path could not be described.
    """
    return _probe(path, None, "anything")


def probe_symlink(path: pathlib.Path) -> FileProbe:
    """Report whether *path* is itself a symbolic link.

    Unlike the probes above this is a predicate rather than an expectation:
    the caller is asking what kind of entry is there, so an entry of another
    kind is an answer, not an occupied path. The link is read without being
    followed, so a link to a missing target is still a link. A refusal is
    still a refusal.

    Returns
    -------
    FileProbe
        Presence when the entry is a link, an absence when there is no entry
        or it is not a link, or the reason the entry could not be described.
    """
    try:
        info = path.lstat()
    except (FileNotFoundError, NotADirectoryError):
        return ABSENT
    except OSError as error:
        return FileProbe(present=False, read_error=f"{path}: {error}")
    return FileProbe(present=stat.S_ISLNK(info.st_mode), read_error=None)


def _probe(
    path: pathlib.Path,
    describes: cabc.Callable[[int], bool] | None,
    expected: str,
) -> FileProbe:
    """Report whether *path* is the kind *describes* accepts, following links.

    The shared core of the expecting probes. *describes* is ``None`` when any
    kind will do; *expected* names the kind for the refusal a mismatch
    produces.

    Returns
    -------
    FileProbe
        Presence, or the reason the path could not be read as *expected*.
    """
    try:
        info = path.stat()
    except FileNotFoundError:
        return _absent_or_dangling(path)
    except NotADirectoryError:
        # A component of the path is a file, so nothing can live beneath it.
        return ABSENT
    except OSError as error:
        return FileProbe(present=False, read_error=f"{path}: {error}")
    if describes is None or describes(info.st_mode):
        return FileProbe(present=True, read_error=None)
    occupant = _non_regular_kind(info.st_mode)
    return FileProbe(
        present=False,
        read_error=f"{path}: {occupant} where {expected} is expected",
    )


def _absent_or_dangling(path: pathlib.Path) -> FileProbe:
    """Tell a missing path from a symbolic link whose target is missing.

    `stat` follows links, so both raise `FileNotFoundError`. `lstat` does not,
    so it succeeds for the link that is there and fails for the path that is
    not. Its own failures are read by the same rule as every other: `ENOENT`
    and `ENOTDIR` mean nothing is there, and anything else is a refusal.

    Returns
    -------
    FileProbe
        An absence, or the refusal naming the unresolved link.
    """
    try:
        path.lstat()
    except (FileNotFoundError, NotADirectoryError):
        return ABSENT
    except OSError as error:
        # The same rule as `probe_file` itself: only the two absence errors
        # mean nothing is there. A refusal to describe the link is a refusal,
        # and swallowing it here would reintroduce one level down exactly the
        # defect this function exists to fix.
        return FileProbe(present=False, read_error=f"{path}: {error}")
    return FileProbe(
        present=False,
        read_error=f"{path}: symbolic link does not resolve",
    )


def _non_regular_kind(mode: int) -> str:
    """Name what occupies a path, for a diagnostic the reader can act on.

    Returns
    -------
    str
        A short description of the file type.
    """
    if stat.S_ISREG(mode):
        return "regular file"
    if stat.S_ISDIR(mode):
        return "directory"
    if stat.S_ISLNK(mode):
        return "symbolic link"
    if stat.S_ISFIFO(mode):
        return "named pipe"
    if stat.S_ISSOCK(mode):
        return "socket"
    return "non-regular file"


def regular_file_exists(path: pathlib.Path, *, operation: str) -> bool:
    """Return whether *path* is a regular file, refusing to guess on failure.

    The raising counterpart to `probe_file`, for a caller whose contract is
    an exception rather than a fact.

    Parameters
    ----------
    path:
        The path to probe.
    operation:
        The stable `operation` identifier reported when the probe fails.

    Returns
    -------
    bool
        Whether *path* exists and is a regular file. An absent path is
        reported as `False`, which is the established no-applicability fact.

    Raises
    ------
    OperationalRuleError
        If the path exists but cannot be inspected, so that an unreadable
        file is never reported as an absent one.
    """
    probe = probe_file(path)
    if probe.read_error is not None:
        # The probe's diagnostic already opens with the path, so this adds
        # only what the caller was trying to do with it.
        message = f"cannot inspect {probe.read_error}"
        raise OperationalRuleError(
            message,
            operation=operation,
            resource=path,
        )
    return probe.present
