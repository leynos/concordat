"""Filesystem probes with a stable failure boundary.

`pathlib.Path.is_file` reports "not a regular file" for a probe it could not
perform, and which failures it swallows has changed between supported Python
versions: 3.13 re-raises a `PermissionError` carrying no `errno`, while 3.14
returns `False`. Either behaviour is wrong for applicability evidence, because
an unreadable manifest is not the same fact as an absent one. Probing through
this module keeps the boundary identical on every supported interpreter.
"""

from __future__ import annotations

import stat
import typing as typ

from concordat.errors import OperationalRuleError

if typ.TYPE_CHECKING:
    import pathlib


def regular_file_exists(path: pathlib.Path, *, operation: str) -> bool:
    """Return whether *path* is a regular file, refusing to guess on failure.

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
    try:
        mode = path.stat().st_mode
    except FileNotFoundError:
        return False
    except OSError as error:
        message = f"cannot inspect {path}: {error}"
        raise OperationalRuleError(
            message,
            operation=operation,
            resource=path,
        ) from error
    return stat.S_ISREG(mode)
