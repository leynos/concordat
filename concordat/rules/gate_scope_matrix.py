"""Expand a workflow job's `strategy.matrix` the way GitHub does.

A matrix is the product of its list axes, less the `exclude` entries, with each
`include` entry added to every original combination it does not contradict or
standing as a combination of its own when it fits none. A matrix with an axis
or list computed by an expression cannot be expanded, and is reported as such
(``None``) rather than expanded with the part it cannot read dropped, because
an `exclude` entry could then miss the combination it names.

`fill` puts a combination's values into a step's script.
"""

from __future__ import annotations

import itertools
import re
import typing as typ

from .makefile_expansion import UNRESOLVED_MARK

__all__ = ["fill", "matrix_combinations"]

_MATRIX_REFERENCE: typ.Final = re.compile(r"\$\{\{\s*matrix\.([\w-]+)\s*\}\}")
_ANY_EXPRESSION: typ.Final = re.compile(r"\$\{\{.*?\}\}")


def _matrix_value(value: object) -> str:
    """Return a matrix value as GitHub Actions would substitute it."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _product(matrix: dict[str, object]) -> list[dict[str, object]]:
    """Return the combinations of the matrix's list-valued axes."""
    axes = {
        key: value
        for key, value in matrix.items()
        if key not in {"include", "exclude"} and isinstance(value, list)
    }
    if not axes:
        # No axes means no original combinations, so each `include` entry
        # becomes a combination of its own.
        return []
    names = list(axes)
    return [
        dict(zip(names, choice, strict=True))
        for choice in itertools.product(*(axes[name] for name in names))
    ]


def _without_excluded(
    combinations: list[dict[str, object]], excludes: list[object]
) -> list[dict[str, object]]:
    """Return *combinations* less those an `exclude` entry matches.

    An entry removes every combination that holds all of its key and value
    pairs. GitHub applies `exclude` before `include`, so an included entry can
    bring back a combination that an exclude removed.

    Returns
    -------
    list[dict[str, object]]
        The combinations that survive.
    """
    entries = [
        typ.cast("dict[str, object]", entry)
        for entry in excludes
        if isinstance(entry, dict)
    ]
    return [
        combination
        for combination in combinations
        if not any(
            all(combination.get(key) == value for key, value in entry.items())
            for entry in entries
        )
    ]


def _with_includes(
    combinations: list[dict[str, object]], includes: list[object]
) -> list[dict[str, object]]:
    """Return *combinations* with each `include` entry applied, as GitHub does.

    An entry is added to every original combination whose original values it
    does not overwrite (a value an earlier `include` added can be overwritten),
    and stands as a combination of its own when it can be added to none. An
    entry that created a combination is not itself matched by later entries.

    Returns
    -------
    list[dict[str, object]]
        The combinations after the entries are applied.
    """
    originals = [dict(combination) for combination in combinations]
    results = [dict(combination) for combination in combinations]
    extra: list[dict[str, object]] = []
    entries = _entries(includes)
    for entry in entries:
        fitting = [
            result
            for original, result in zip(originals, results, strict=True)
            if _fits(original, entry)
        ]
        for result in fitting:
            result.update(entry)
        if not fitting:
            extra.append(dict(entry))
    return [*results, *extra]


def _fits(original: dict[str, object], entry: dict[str, object]) -> bool:
    """Return whether *entry* overwrites none of *original*'s values."""
    return all(original[key] == entry[key] for key in entry.keys() & original.keys())


def _entries(includes: list[object]) -> list[dict[str, object]]:
    """Return the `include` entries that are mappings."""
    return [
        typ.cast("dict[str, object]", candidate)
        for candidate in includes
        if isinstance(candidate, dict)
    ]


def _strategy_matrix(job: object) -> object | None:
    """Return a job's `strategy.matrix` as written, or ``None`` when it has none."""
    strategy = job.get("strategy") if isinstance(job, dict) else None
    return strategy.get("matrix") if isinstance(strategy, dict) else None


def _readable_axes(matrix: object) -> dict[str, object] | None:
    """Return the matrix's axes when every part of it can be read, else ``None``.

    An axis that is not a list is computed by an expression. Dropping it would
    leave combinations an `exclude` entry could not match, so the matrix is not
    expanded at all; nor is one whose `include` or `exclude` is not a list.

    Returns
    -------
    dict[str, object] | None
        The axes, or ``None`` when the matrix cannot be expanded here.
    """
    if not isinstance(matrix, dict):
        return None
    axes = typ.cast("dict[str, object]", matrix)
    entries = (axes.get("include", []), axes.get("exclude", []))
    if not all(isinstance(entry, list) for entry in entries):
        return None
    computed = (
        not isinstance(value, list)
        for key, value in axes.items()
        if key not in {"include", "exclude"}
    )
    return None if any(computed) else axes


def matrix_combinations(job: object) -> list[dict[str, object]] | None:
    """Return the combinations a job's `strategy.matrix` expands to.

    Returns
    -------
    list[dict[str, object]] | None
        One mapping per matrix entry, a single empty one for a job with no
        matrix, or ``None`` when the matrix is computed by an expression and
        cannot be expanded here.
    """
    matrix = _strategy_matrix(job)
    if matrix is None:
        return [{}]
    axes = _readable_axes(matrix)
    if axes is None:
        return None
    includes, excludes = axes.get("include", []), axes.get("exclude", [])
    combinations = _with_includes(
        _without_excluded(_product(axes), typ.cast("list[object]", excludes)),
        typ.cast("list[object]", includes),
    )
    # A matrix with no axes and no entries still runs its job once.
    return combinations or [{}]


def fill(text: str, combination: dict[str, object]) -> str:
    """Return *text* with `${{ matrix.* }}` filled in and other expressions marked."""

    def matrix(match: re.Match[str]) -> str:
        """Return one matrix reference's value, or the mark when it has none."""
        value = combination.get(match.group(1))
        return UNRESOLVED_MARK if value is None else _matrix_value(value)

    return _ANY_EXPRESSION.sub(UNRESOLVED_MARK, _MATRIX_REFERENCE.sub(matrix, text))
