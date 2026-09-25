"""Recognize coreutils ``install`` placing a downloaded binary on `PATH`.

A tool provisioned from a release asset is not installed by a package
manager: the lane downloads it, verifies its digest, and copies it into a
directory it adds to `PATH` with ``install -m 0755 SRC DEST``. The
provisioning contract in ``gate_provisioning_support`` recognizes only
package-manager ``install`` verbs, so this module supplies the one other
shape it accepts. It is used only by ``installed_tool_names`` there.

The recognizer errs towards not recognizing, as that module's does: a
directory-creating ``install -d``, a command with fewer than two operands, and
a destination whose file name is itself a shell expansion all provision
nothing.
"""

from __future__ import annotations

import typing as typ

if typ.TYPE_CHECKING:
    import collections.abc as cabc

# Options of coreutils `install` that take a separate argument, which must not
# be read as an operand.
_OPTIONS_WITH_ARGUMENT: typ.Final = frozenset({"-m", "-o", "-g", "-S", "-t"})
_DIRECTORY_OPTIONS: typ.Final = frozenset({"-d", "--directory", "-t"})


def installed_file_name(arguments: cabc.Sequence[str]) -> frozenset[str]:
    """Return the executable a coreutils ``install`` command places, if any.

    Parameters
    ----------
    arguments:
        The command's tokens after the ``install`` program name.

    Returns
    -------
        The destination's file name, or an empty set when the command does
        not install one file under a literal name.

    Examples
    --------
    >>> sorted(installed_file_name(["-m", "0755", "dl/asset", "${bin}/makeutil"]))
    ['makeutil']
    >>> sorted(installed_file_name(["-d", "${bin}"]))
    []
    """
    if _DIRECTORY_OPTIONS.intersection(arguments):
        return frozenset()
    operands = _operands(arguments)
    if len(operands) != 2:
        return frozenset()
    name = operands[-1].rsplit("/", 1)[-1]
    if not name or "$" in name:
        return frozenset()
    return frozenset({name})


def _operands(arguments: cabc.Sequence[str]) -> list[str]:
    """Return the command's operands, skipping options and their arguments.

    Returns
    -------
        The source and destination tokens, in order.

    Examples
    --------
    >>> _operands(["-D", "-m", "0755", "dl/asset", "bin/makeutil"])
    ['dl/asset', 'bin/makeutil']
    """
    operands: list[str] = []
    is_option_argument = False
    for token in arguments:
        if is_option_argument:
            is_option_argument = False
            continue
        is_option_argument = token in _OPTIONS_WITH_ARGUMENT
        if not token.startswith("-"):
            operands.append(token)
    return operands
