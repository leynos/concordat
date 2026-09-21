"""Filesystem probes that tell "not there" apart from "could not look".

`Path.exists`, `Path.is_file`, `Path.is_dir`, and `Path.is_symlink` answer
``False`` for an unreadable path as readily as for an absent one, and from
Python 3.14 they suppress every `OSError` the operating system raises. This
package supports 3.13 and later, so the same checkout would raise on one
interpreter and read as empty on the other.

An audit cannot accept either reading. A policy input it could not examine is
not a policy input that is absent: the first is an operational failure to
report, the second a fact about the repository. These readers draw that line
once, so every caller inherits it.
"""

from __future__ import annotations

import errno
import stat as stat_module
import typing as typ

from concordat.errors import OperationalRuleError

if typ.TYPE_CHECKING:
    import os
    import pathlib

# The two errno values that mean the path genuinely is not there. Every other
# `OSError` is the audit failing to look, not a fact about the checkout.
ABSENT_ERRNOS: typ.Final = frozenset({errno.ENOENT, errno.ENOTDIR})


def stat_or_absent(
    path: pathlib.Path, operation: str, *, follow_symlinks: bool = True
) -> os.stat_result | None:
    """Return the path's status, or ``None`` when it does not exist.

    Parameters
    ----------
    path:
        The path to examine.
    operation:
        The audit operation to name in an `OperationalRuleError`.
    follow_symlinks:
        Whether to resolve a symbolic link before reading its status.

    Returns
    -------
    os.stat_result | None
        The status, or ``None`` when the path or one of its parents is
        genuinely missing.

    Raises
    ------
    OperationalRuleError
        If the path cannot be examined for any other reason.
    """
    try:
        return path.stat() if follow_symlinks else path.lstat()
    except OSError as error:
        if error.errno in ABSENT_ERRNOS:
            return None
        message = f"cannot examine {path}: {error}"
        raise OperationalRuleError(
            message, operation=operation, resource=path
        ) from error


def exists(path: pathlib.Path, operation: str) -> bool:
    """Return whether the path exists, raising if it cannot be examined.

    Returns
    -------
    bool
        Whether the path is there.
    """
    return stat_or_absent(path, operation) is not None


def is_file(path: pathlib.Path, operation: str) -> bool:
    """Return whether the path is a regular file, following symbolic links.

    Returns
    -------
    bool
        Whether the path is a regular file.
    """
    status = stat_or_absent(path, operation)
    return status is not None and stat_module.S_ISREG(status.st_mode)


def is_dir(path: pathlib.Path, operation: str) -> bool:
    """Return whether the path is a directory, following symbolic links.

    Returns
    -------
    bool
        Whether the path is a directory.
    """
    status = stat_or_absent(path, operation)
    return status is not None and stat_module.S_ISDIR(status.st_mode)


def is_symlink(path: pathlib.Path, operation: str) -> bool:
    """Return whether the path is itself a symbolic link.

    Returns
    -------
    bool
        Whether the path is a symbolic link.
    """
    status = stat_or_absent(path, operation, follow_symlinks=False)
    return status is not None and stat_module.S_ISLNK(status.st_mode)
