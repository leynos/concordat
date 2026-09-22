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

    Returns
    -------
    FileProbe
        Presence, or the filesystem's diagnostic when the path could not be
        described.
    """
    try:
        info = path.stat()
    except (FileNotFoundError, NotADirectoryError):
        # A missing path, or one whose parent is not a directory: both mean
        # the file is not there, which is an answer rather than a failure.
        return ABSENT
    except OSError as error:
        return FileProbe(present=False, read_error=str(error))
    if not stat.S_ISREG(info.st_mode):
        # A directory or device in the file's place is not the file, and the
        # filesystem answered the question, so it is an absence.
        return ABSENT
    return FileProbe(present=True, read_error=None)


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
        message = f"cannot inspect {path}: {probe.read_error}"
        raise OperationalRuleError(
            message,
            operation=operation,
            resource=path,
        )
    return probe.present
