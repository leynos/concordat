"""Distinguish an absent file from one the filesystem refused to describe.

`pathlib.Path.is_file()` answers both questions with `False`: a path that does
not exist and a path whose `stat` failed on permissions, a broken mount, or a
filename the filesystem rejects are indistinguishable. Every rule fact built on
that probe would report an unreadable checkout as a compliant absence, which is
the one answer a fail-closed audit must never give by accident.

The probe here separates the two. Callers turn an absence into whatever their
clause means by it, and a refusal into a fail-closed fact carrying the reason.

Two shapes are offered over one probe. `probe_file` returns the outcome as a
fact, for callers that carry the reason into a policy clause.
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
    try:
        info = path.stat()
    except FileNotFoundError:
        return _absent_or_dangling(path)
    except NotADirectoryError:
        # A component of the path is a file, so nothing can live beneath it.
        return ABSENT
    except OSError as error:
        return FileProbe(present=False, read_error=f"{path}: {error}")
    if stat.S_ISREG(info.st_mode):
        return FileProbe(present=True, read_error=None)
    occupant = _non_regular_kind(info.st_mode)
    return FileProbe(
        present=False,
        read_error=f"{path}: {occupant} where a file is expected",
    )


def _absent_or_dangling(path: pathlib.Path) -> FileProbe:
    """Tell a missing path from a symbolic link whose target is missing.

    `stat` follows links, so both raise `FileNotFoundError`. `lstat` does not,
    so it succeeds for the link that is there and fails for the path that is
    not.

    Returns
    -------
    FileProbe
        An absence, or the refusal naming the unresolved link.
    """
    try:
        path.lstat()
    except OSError:
        return ABSENT
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
