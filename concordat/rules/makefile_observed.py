"""Observed `makeutil parse` runs for the envelope builders.

`inspect_makefile` is a query: it reads no clock and writes no log. The
envelope builders are the orchestration boundary, so they call
:func:`inspect_makefile_observed`, which times the query and reports one event
per parse through an injected emitter.
"""

from __future__ import annotations

import logging
import subprocess
import time
import typing as typ

from concordat.errors import OperationalRuleError

from .makefile_facts import (
    OPERATION_PARSE_MAKEFILE,
    TOOL_MAKEUTIL,
    MakefileFacts,
    MakefileRefusedError,
    inspect_makefile,
)

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    import pathlib

_logger = logging.getLogger(__name__)


class MakefileParseEvent(typ.NamedTuple):
    """One `makeutil parse` run: a fixed outcome word and how long it took."""

    operation: str
    tool: str
    outcome: str
    elapsed_seconds: float


type ParseEmitter = cabc.Callable[[MakefileParseEvent], None]
type Clock = cabc.Callable[[], float]


def log_parse_event(event: MakefileParseEvent) -> None:
    """Record *event* as a debug log entry with structured fields."""
    _logger.debug(
        "makeutil parse finished",
        extra={
            "operation": event.operation,
            "tool": event.tool,
            "outcome": event.outcome,
            "elapsed_seconds": event.elapsed_seconds,
        },
    )


def _failure_category(error: OperationalRuleError) -> str:
    """Return a fixed word for why `makeutil parse` failed."""
    match error.__cause__:
        case subprocess.TimeoutExpired():
            return "timeout"
        case OSError():
            return "launch-failure"
        case _:
            return "error"


def inspect_makefile_observed(
    path: pathlib.Path,
    *,
    timeout: float = 10.0,
    clock: Clock = time.perf_counter,
    emit: ParseEmitter = log_parse_event,
) -> MakefileFacts:
    """Run :func:`inspect_makefile` and emit one event describing the run.

    The outcome is one of ``complete``, ``recovered``, ``refused``,
    ``timeout``, ``launch-failure`` or ``error``: a fixed word, never the
    tool's own output, which can quote Makefile content.

    Returns
    -------
    MakefileFacts
        Validated Makefile facts from the makeutil report.

    Raises
    ------
    MakefileRefusedError
        If `makeutil` exits with its refusal status.
    OperationalRuleError
        If `makeutil` cannot be run, or its exit status or report is unusable.
    """
    started = clock()
    outcome = "error"
    try:
        facts = inspect_makefile(path, timeout=timeout)
    except MakefileRefusedError:
        outcome = "refused"
        raise
    except OperationalRuleError as error:
        outcome = _failure_category(error)
        raise
    else:
        outcome = facts.status
        return facts
    finally:
        emit(
            MakefileParseEvent(
                operation=OPERATION_PARSE_MAKEFILE,
                tool=TOOL_MAKEUTIL,
                outcome=outcome,
                elapsed_seconds=round(clock() - started, 3),
            )
        )
