r"""Decide whether an environment call could name `CARGO_NET_OFFLINE`.

A call on a bound command that runs on only some paths is recorded as an
operation the reader cannot see, because it may write or remove the offline
variable. `env("CARGO_TARGET_DIR", dir)` and `env_remove("RUSTFLAGS")` name a
variable the reader can read, and it is not the offline one, so whether they run
says nothing about the network. Every other form may touch it: a name the reader
cannot read might be it; a name written with an escape (`"\x43ARGO_NET_OFFLINE"`)
is not read; and a different spelling of the offline name counts as the offline
name, because Windows compares environment names without regard to case.
"""

from __future__ import annotations

import re
import typing as typ

OFFLINE_VARIABLE: typ.Final = "CARGO_NET_OFFLINE"
_PLAIN_VARIABLE: typ.Final = re.compile(r"\w+")


def is_other_variable(name: str | None) -> bool:
    """Report whether *name* is plainly a variable other than the offline one.

    Returns
    -------
    bool
        True for a name made of identifier characters only, whatever its case,
        that does not spell `CARGO_NET_OFFLINE`; an escape or other punctuation
        means the reader has not seen the name the program will use.
    """
    return (
        name is not None
        and _PLAIN_VARIABLE.fullmatch(name) is not None
        and name.upper() != OFFLINE_VARIABLE
    )


def may_include_offline_variable(names: typ.Sequence[str | None] | None) -> bool:
    """Report whether a call that names *names* could touch the offline variable.

    Returns
    -------
    bool
        False only when the call names at least one variable and every name is
        plainly another variable. None (an unreadable list) and an empty list
        may hide the offline name.
    """
    return not names or not all(is_other_variable(name) for name in names)
