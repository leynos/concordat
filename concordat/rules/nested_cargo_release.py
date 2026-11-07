"""Recognise the workflow steps cargo-dist generates to install its own tools.

A cargo-dist release workflow installs dist, and the packages it needs, with
`run: ${{ matrix.install_dist.run }}`: a command the plan job writes into the
matrix. The step has no text to read, but in a job that goes on to run
`dist build` it installs a tool and runs no test. Anything else that is only an
expression stays unread.
"""

from __future__ import annotations

import re
import typing as typ

if typ.TYPE_CHECKING:
    import collections.abc as cabc

# A step that is nothing but one matrix lookup.
_MATRIX_LOOKUP: typ.Final = re.compile(r"\s*\$\{\{\s*matrix\.[^}]*\}\}\s*")
# A command line that starts cargo-dist: the job builds release artefacts.
_DIST_COMMAND: typ.Final = re.compile(r"^\s*(?:cargo\s+)?dist\s+[a-z]", re.MULTILINE)


def runs_dist(steps: cabc.Sequence[object]) -> bool:
    """Return whether any step of a job runs a cargo-dist subcommand."""
    for raw in steps:
        run = raw.get("run") if isinstance(raw, dict) else None
        if isinstance(run, str) and _DIST_COMMAND.search(run) is not None:
            return True
    return False


def is_matrix_lookup(run: str) -> bool:
    """Return whether *run* is a single `${{ matrix.* }}` lookup and nothing else."""
    return _MATRIX_LOOKUP.fullmatch(run) is not None
