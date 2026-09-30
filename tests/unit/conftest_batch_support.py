"""Evaluate many policy envelopes against one rule in a single Conftest process.

Property tests judge generated envelopes against a rule package's policy. One
Conftest process per case made those tests slow enough to trip the suite's
30 s timeout on a loaded host, so they draw a fixed-size list of cases and
evaluate it here, paying for one process per list. The helper lives with the
tests because no production caller evaluates more than one checkout at a time.
"""

from __future__ import annotations

import pathlib
import typing as typ

from concordat.rules import runner

if typ.TYPE_CHECKING:
    import collections.abc as cabc

    from concordat.rules.envelope import PolicyEnvelope


def invoke_conftest_batch(
    rule_id: str,
    envelopes: cabc.Sequence[PolicyEnvelope],
) -> list[list[runner._ConftestResult]]:
    """Evaluate every envelope in one Conftest process, one result list each.

    Conftest names the input file in each result, so results are matched to
    their envelopes by name rather than by position; positional matching would
    swap verdicts if Conftest ever reordered its output.

    Returns
    -------
    list[list[runner._ConftestResult]]
        The results for each envelope, in the order the envelopes were given.

    Raises
    ------
    runner._conftest_shape_error
        If Conftest returns no result for one of the envelopes, so a dropped
        result cannot pass as a clean one.
    """
    results = runner._evaluate_envelopes(rule_id, envelopes)
    grouped: dict[str, list[runner._ConftestResult]] = {}
    for result in results:
        name = pathlib.Path(result.get("filename", "")).name
        grouped.setdefault(name, []).append(result)
    names = runner._envelope_file_names(len(envelopes))
    missing = [name for name in names if name not in grouped]
    if missing:
        label = f"no result for {', '.join(missing)}"
        raise runner._conftest_shape_error(label, rule_id, f"{len(results)} results")
    return [grouped[name] for name in names]
