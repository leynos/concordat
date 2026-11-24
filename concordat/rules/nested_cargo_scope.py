"""Discover which Rust files a checkout's compiler builds as test code.

The spawn reader needs to know, for each file, whether a build in it runs in a
test, and which targets compile it. Both answers come from the same walk over
`mod` declarations, so the walk lives here, apart from the fact collection that
consumes its result.
"""

from __future__ import annotations

import typing as typ

from .nested_cargo_modules import is_crate_root, resolve_declarations, scan_scope
from .nested_cargo_reach import Reach
from .nested_cargo_source import (
    RustSource,
    TestScope,
    read_source,
    whole_file_scope,
)

if typ.TYPE_CHECKING:
    import pathlib


def _scan_source(
    checkout: pathlib.Path,
    fixtures: frozenset[str],
    source: RustSource,
    scopes: dict[pathlib.PurePosixPath, TestScope],
    reaches: dict[pathlib.PurePosixPath, Reach],
) -> tuple[list[str], list[pathlib.PurePosixPath]]:
    """Scan one file in place, reporting its problems and the files it moved.

    The scope map is updated with what this scan found, and the reach map with
    how each file this file declares was reached. Both are shared state the
    discovery loop reads back on its next pass.

    Returns
    -------
    tuple[list[str], list[pathlib.PurePosixPath]]
        The problems found in this scan, and the files whose scope or reach it
        changed.
    """
    known = scopes[source.relative]
    found_problems: list[str] = []
    scope, declarations = scan_scope(source, found_problems, known)
    if known.whole_file:
        # A module declaration already proved this whole file is test code.
        # Rescanning it must not overwrite that stronger claim with the
        # narrower attribute ranges this pass found.
        known.ranges = scope.ranges
    else:
        scopes[source.relative] = scope
    found = resolve_declarations(
        checkout,
        source,
        declarations,
        scopes,
        reaches,
        fixtures,
        found_problems,
    )
    return found_problems, [*found.fresh, *found.touched]


def _first_scans(
    sources: list[RustSource],
    scopes: dict[pathlib.PurePosixPath, TestScope],
    seeds: dict[pathlib.PurePosixPath, Reach],
) -> list[RustSource]:
    """Return the sources whose declarations are read before anything reaches them.

    A file's `mod` declarations only bind if the compiler builds the file.
    Ordinary library code is scanned so its `#[cfg(test)]` items are found. A
    whole-file test source is scanned only when Cargo builds it as a target (or
    a seed says an owner outside the resolver's view does): the rest are text
    that no compiler reaches — a trybuild case, or a file another test
    `include!`s — and their `mod` lines name files that nothing declares, which
    would invent a finding rather than report one.

    Returns
    -------
    list[RustSource]
        The sources to scan first.
    """
    return [
        source
        for source in sources
        if not scopes[source.relative].whole_file
        or is_crate_root(source.relative)
        or source.relative in seeds
    ]


def discover_scope(
    checkout: pathlib.Path,
    fixtures: frozenset[str],
    sources: list[RustSource],
    problems: list[str],
    seeds: dict[pathlib.PurePosixPath, Reach],
) -> tuple[dict[pathlib.PurePosixPath, TestScope], dict[pathlib.PurePosixPath, Reach]]:
    """Return the scope of every file the checkout's compiler builds as test code.

    A file's scope and reach are both inputs to its own scan: a declaration
    pulled into test scope is test code throughout, and a file reached by
    `#[path]` resolves its own declarations beside it rather than under a stem
    directory. Each of those answers can arrive after the file was first read,
    so the scan repeats until neither moves — and a declaration that names a
    file nothing declares in an early pass is only reported once the pass that
    settles its owner has run.

    Files are appended to *sources* as declarations reach them, so the caller
    keeps the complete set to report from. The *seeds* are files with an owner
    the resolver cannot see; they start with that owner recorded and are
    scanned at once, so what they declare inherits it.

    Returns
    -------
    tuple[dict[pathlib.PurePosixPath, TestScope], dict[pathlib.PurePosixPath, Reach]]
        The scope of every source read, keyed by its root-relative path, and
        how each declared file was reached, including the targets that reach it.
    """
    scopes = {
        source.relative: TestScope(whole_file_scope(source.relative))
        for source in sources
    }
    reaches = dict(seeds)
    by_relative = {source.relative: source for source in sources}
    # The scope and reach each file was scanned under. A file's declarations
    # resolve differently once either moves, so the pair is what decides
    # whether an earlier scan is still current.
    scanned: dict[pathlib.PurePosixPath, tuple[bool, Reach]] = {}
    # A declaration can name no file in an early pass and resolve once the
    # reach of its own file is known, so each scan's findings replace the last
    # and only the final set for each file is reported.
    reported: dict[pathlib.PurePosixPath, list[str]] = {}
    pending = _first_scans(sources, scopes, seeds)
    while pending:
        source = pending.pop()
        scanned[source.relative] = (
            scopes[source.relative].whole_file,
            reaches.get(source.relative, Reach()),
        )
        found_problems, moved = _scan_source(
            checkout, fixtures, source, scopes, reaches
        )
        reported[source.relative] = found_problems
        for relative in moved:
            state = (scopes[relative].whole_file, reaches.get(relative, Reach()))
            if scanned.get(relative) == state:
                continue
            known_source = by_relative.get(relative)
            if known_source is None:
                known_source, error = read_source(checkout, relative)
                if error is not None:
                    problems.append(error)
                if known_source is None:
                    continue
                sources.append(known_source)
                by_relative[relative] = known_source
            pending.append(known_source)
    for source in sources:
        problems.extend(reported.get(source.relative, ()))
    return scopes, reaches
