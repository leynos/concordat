"""Read the names a repository uses for its preparation and compiling helpers.

The cold-build rule asks a repository to declare where a nested build is paid
for. How it names that declaration is the repository's choice, so the names are
rule parameters rather than constants: the functions that gate a timed test on
prepared artefacts, the patterns that match a test which prepares them, and the
harness functions that start a build in their caller's behalf.
"""

from __future__ import annotations

import dataclasses
import re
import typing as typ

if typ.TYPE_CHECKING:
    import collections.abc as cabc


@dataclasses.dataclass(frozen=True, slots=True)
class Gates:
    """Hold the names a repository uses for its preparation and compiling helpers.

    Attributes
    ----------
    functions:
        Names of functions that gate a timed test on prepared artefacts.
    tests:
        Patterns matched against test function names that prepare artefacts.
    compiling_calls:
        Names of harness functions that build in the caller's behalf.
    """

    functions: tuple[str, ...]
    tests: tuple[re.Pattern[str], ...]
    compiling_calls: tuple[str, ...]


def configured_strings(value: object) -> tuple[str, ...]:
    """Return the non-empty string items of a configured list, ignoring the rest."""
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, str) and item)


def _patterns(texts: cabc.Iterable[str]) -> tuple[re.Pattern[str], ...]:
    """Compile each pattern, skipping one that is not a regular expression.

    The rule would otherwise refuse to run over a bad parameter, and the policy
    reports nothing it cannot read.

    Returns
    -------
    tuple[re.Pattern[str], ...]
        The patterns that compile, in the order given.
    """
    compiled: list[re.Pattern[str]] = []
    for text in texts:
        try:
            compiled.append(re.compile(text))
        except re.error:
            continue
    return tuple(compiled)


def read_gates(parameters: cabc.Mapping[str, object] | None) -> Gates:
    """Return the preparation names the rule's parameters configure.

    Returns
    -------
    Gates
        The configured function names, test patterns and compiling helpers.
    """
    configured = (parameters or {}).get("preparation_gates")
    block = configured if isinstance(configured, dict) else {}
    return Gates(
        functions=configured_strings(block.get("functions")),
        tests=_patterns(configured_strings(block.get("tests"))),
        compiling_calls=configured_strings((parameters or {}).get("compiling_calls")),
    )
