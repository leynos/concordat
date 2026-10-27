"""Find the cargo spawns and trybuild uses in a checkout's test scope.

A test that starts `cargo` resolves dependencies and may update the crates.io
index while holding the shared package-cache lock. Whether it does depends on
evidence the compiler alone decides, so this module reads Rust source as text
and reports what it can prove. :mod:`nested_cargo_commands` reads one command's
network reach and :mod:`nested_cargo_source` decodes the files and their
whole-file test scope; this module supplies the rest of the scope — the
`#[cfg(test)]` items, the `mod name;` declarations they hold, and the files
those declarations name — and reports the facts the policy acts on.

Scope follows the rule's terms. A `#[cfg(test)]` item is test code only for the
span of that item, and a `mod name;` declaration in test scope is resolved to
the file it names — whether the declaration carries the attribute itself or
sits inside a `#[cfg(test)]` module, which a test build compiles just the same.
A fixture package's sources are nested-build input rather than
host test code, and are excluded by the two selectors that decide what a
fixture package is.

The reader never reports `offline` on a guess and never reports `online` on
one: an unreadable source is a problem fact, not a clean bill of health.
"""

from __future__ import annotations

import dataclasses
import operator
import pathlib
import re
import typing as typ

from .dependency_family_coverage import mapping
from .dependency_family_discovery import cargo_tables
from .nested_cargo import is_fixture_directory, standalone_directories
from .nested_cargo_commands import (
    COMMAND_NEW,
    CargoCommand,
    bound_name,
    collect_variable,
    command_state,
    construction,
    follow_chain,
    has_cargo_construction,
    helper_body,
    unreached_helper,
)
from .nested_cargo_modules import (
    Reach,
    is_crate_root,
    resolve_declarations,
    scan_scope,
)
from .nested_cargo_source import (
    RustSource,
    TestScope,
    brace_pairs,
    enclosing_block,
    in_fixture,
    line_of,
    read_source,
    read_sources,
    whole_file_scope,
)
from .rust_source import scan_balanced

if typ.TYPE_CHECKING:
    from .nested_cargo import Manifest


class SpawnFact(typ.TypedDict):
    """Store one cargo process construction found in test scope.

    Attributes
    ----------
    path:
        The source file's path relative to the checkout, in POSIX form.
    line:
        The line the construction or helper call starts on.
    form:
        `command_new`, `env_cargo`, or `helper`.
    state:
        `offline`, `online`, or `unresolved`.
    flags:
        The literal arguments that look like flags, in source order.
    environment:
        The environment variables the command sets itself, in source order.
    """

    path: str
    line: int
    form: str
    state: str
    flags: list[str]
    environment: list[str]


class TrybuildFact(typ.TypedDict):
    """Store one piece of evidence that a test drives trybuild.

    Attributes
    ----------
    path:
        The manifest or source file's path relative to the checkout.
    line:
        The line the reference starts on; a manifest fact is line 1.
    form:
        `manifest` for a dependency declaration, `source` for a reference.
    """

    path: str
    line: int
    form: str


@dataclasses.dataclass(frozen=True, slots=True)
class RustEvidence:
    """Store the Rust-side evidence the nested-cargo offline rule judges.

    Attributes
    ----------
    spawns:
        Every cargo construction in test scope that may resolve dependencies.
    trybuild:
        Every manifest declaration or source reference that drives trybuild.
    """

    spawns: list[SpawnFact]
    trybuild: list[TrybuildFact]


# The package a test uses to compile its UI cases. It is named here rather than
# inline because the manifest reader resolves renames against it and the source
# reader falls back to it when no manifest declares one.
TRYBUILD_PACKAGE: typ.Final = "trybuild"

_HELPER: typ.Final = re.compile(r"\bfn\s+(?P<name>[A-Za-z_]\w*)\s*\(")
_DECLARATION: typ.Final = re.compile(r"\bfn\s*$")


def _fact(source: RustSource, command: CargoCommand, state: str) -> SpawnFact:
    """Return the policy fact for one classified command."""
    return {
        "path": source.relative.as_posix(),
        "line": line_of(source.text, command.start),
        "form": command.form,
        "state": state,
        "flags": [
            word
            for word in command.words
            if isinstance(word, str) and word.startswith("-")
        ],
        "environment": list(command.environment),
    }


def _helper_names(source: RustSource) -> dict[str, tuple[int, int]]:
    """Return each cargo-command helper's body span, by function name.

    Returns
    -------
    dict[str, tuple[int, int]]
        The body `(open, end)` of every `fn ... -> Command` that builds cargo.
    """
    found: dict[str, tuple[int, int]] = {}
    for match in _HELPER.finditer(source.masked):
        body = helper_body(source.masked, match)
        if body is None:
            continue
        name, start, end = body
        if has_cargo_construction(source, start, end):
            found[name] = (start, end)
    return found


def _in_ranges(offset: int, ranges: typ.Iterable[tuple[int, int]]) -> bool:
    """Report whether *offset* lies in any of *ranges*."""
    return any(start <= offset < end for start, end in ranges)


def _direct_commands(
    source: RustSource,
    helpers: dict[str, tuple[int, int]],
    pairs: list[tuple[int, int]],
) -> list[CargoCommand]:
    """Return the cargo commands this source builds outside a helper body.

    Returns
    -------
    list[CargoCommand]
        The constructions, in source order.
    """
    bodies = list(helpers.values())
    found: list[CargoCommand] = []
    for match in COMMAND_NEW.finditer(source.masked):
        if _in_ranges(match.start(), bodies):
            continue
        command = construction(source, match, pairs)
        if command is not None:
            found.append(command)
    return found


def _definition(
    source: RustSource, body: tuple[int, int], pairs: list[tuple[int, int]]
) -> CargoCommand | None:
    """Return the command a helper function itself builds, if it is readable.

    Returns
    -------
    CargoCommand | None
        The first cargo construction in the body, or None.
    """
    for match in COMMAND_NEW.finditer(source.masked, body[0], body[1]):
        command = construction(source, match, pairs)
        if command is not None:
            return command
    return None


def _helper_sites(
    source: RustSource,
    name: str,
    body: tuple[int, int],
    pairs: list[tuple[int, int]],
) -> list[CargoCommand]:
    """Return the call sites of one helper and the commands they build.

    Returns
    -------
    list[CargoCommand]
        One command per call site outside the helper's own body.
    """
    pattern = re.compile(rf"\b{re.escape(name)}\s*\(")
    found: list[CargoCommand] = []
    for match in pattern.finditer(source.masked):
        if body[0] < match.start() < body[1]:
            continue
        if _DECLARATION.search(
            source.masked[max(0, match.start() - 8) : match.start()]
        ):
            continue
        close = scan_balanced(source.masked, match.end() - 1)
        if close is None:
            continue
        command = CargoCommand(start=match.start(), form="helper")
        after = follow_chain(source, command, close)
        block = enclosing_block(pairs, match.start())
        bound = bound_name(source.masked, match.start())
        if block is not None and bound is not None:
            collect_variable(source, command, bound, after, block[1])
        found.append(command)
    return found


def _source_facts(source: RustSource, scope: TestScope) -> list[SpawnFact]:
    """Return the spawn facts of one source.

    A construction written in the file is recorded whatever its state, so the
    envelope carries the offline ones as evidence of what was examined. A
    helper's own body is a definition rather than a spawn, so only its call
    sites are recorded, and only while they can still reach the network.

    A helper with no call site in its own file is reported at its definition
    instead, as a command the reader cannot settle. The call may live in
    another file, where whatever it adds is invisible; skipping the
    construction altogether would let a nested build leave the audit with no
    fact at all.

    Returns
    -------
    list[SpawnFact]
        The reportable commands, in source order.
    """
    pairs = brace_pairs(source.masked)
    helpers = _helper_names(source)
    direct = [
        command
        for command in _direct_commands(source, helpers, pairs)
        if scope.contains(command.start)
    ]
    sites: list[CargoCommand] = []
    for name, body in helpers.items():
        definition = _definition(source, body, pairs)
        if definition is None:
            continue
        calls = [
            site
            for site in _helper_sites(source, name, body, pairs)
            if scope.contains(site.start)
        ]
        # A helper with no call site in this file may be called from another,
        # and a call proven offline does not carry the helper's other call
        # sites with it. Either way the definition is reported once, as
        # unreadable, so the construction never drops out of the audit.
        #
        # Only a definition that is itself test code is reported. A helper in
        # library code is outside the reader's scope and stays outside it: its
        # call sites are visible here, and library code is not something this
        # rule judges. Reporting it would put an unsuppressable EN-001 on a
        # file the rule promises to leave alone.
        if not calls and scope.contains(definition.start):
            sites.append(unreached_helper(definition))
        sites.extend(site for site in calls if not _absorbed(site, definition))
    direct_facts = [
        _fact(source, command, state)
        for command in direct
        if (state := command_state(command)) is not None
    ]
    site_facts = [
        _fact(source, command, state)
        for command in sites
        if (state := command_state(command)) not in {None, "offline"}
    ]
    return sorted(direct_facts + site_facts, key=operator.itemgetter("line"))


def _absorbed(site: CargoCommand, definition: CargoCommand) -> bool:
    """Take on a helper's evidence and report whether it proves the site offline.

    Returns
    -------
    bool
        True when the call site, once the helper's own arguments and environment
        are counted, cannot reach the network.
    """
    site.absorb(definition)
    return command_state(site) == "offline"


def _test_literal(aliases: frozenset[str]) -> re.Pattern[str]:
    """Return the pattern matching a trybuild `TestCases` reference.

    Cargo lets a dependency rename the package it pulls in, so the crate may
    be in scope as `tb` rather than `trybuild`. Only the names the workspace
    actually declares are matched: a `TestCases` path through any other name
    belongs to some other crate. *aliases* holds the names as the manifests
    write them, which is not always how source spells them.

    Returns
    -------
    re.Pattern[str]
        The pattern for a reference through one of *aliases*.
    """
    # A dependency key is written in the manifest, but Rust path segments
    # cannot contain hyphens: Cargo exposes `my-trybuild` to source as
    # `my_trybuild`. Matching the key as written would build a pattern for a
    # name no file can use.
    in_source = {name.replace("-", "_") for name in aliases}
    # Longest first, so an alias that is a prefix of another cannot shadow it.
    joined = "|".join(
        re.escape(name) for name in sorted(in_source, key=len, reverse=True)
    )
    return re.compile(rf"\b(?:{joined})\s*::\s*TestCases")


def _dependency_package(key: str, spec: object) -> str:
    """Return the package a dependency entry pulls in.

    A rename is written as `key = { package = "name", ... }`, and the key is
    then the local name, not the package. A bare version string leaves the key
    as the package name.

    Returns
    -------
    str
        The declared package name.
    """
    renamed = mapping(spec)
    if renamed is not None:
        declared = renamed.get("package")
        if isinstance(declared, str):
            return declared
    return key


def _aliases_by_directory(
    checkout: pathlib.Path, manifests: typ.Mapping[pathlib.Path, Manifest]
) -> dict[pathlib.PurePosixPath, frozenset[str]]:
    """Return the local names each manifest gives the trybuild dependency.

    A manifest governs the files at and below its own directory, so the
    directory is the key: a source file is read with the names its own package
    declares, not with another package's.

    The plain package name is always included. `trybuild::TestCases` names the
    crate however the dependency was declared, so matching it needs no
    manifest, and requiring one would drop a real reference from a file whose
    manifest is a virtual workspace root that declares no dependencies at all.

    Returns
    -------
    dict[pathlib.PurePosixPath, frozenset[str]]
        Trybuild's local names, by the manifest's root-relative directory.
    """
    aliases: dict[pathlib.PurePosixPath, frozenset[str]] = {}
    for path, document in manifests.items():
        relative = pathlib.PurePosixPath(path.relative_to(checkout).as_posix())
        aliases[relative.parent] = frozenset(
            {TRYBUILD_PACKAGE}
            | {
                key
                for _section, _target, table in cargo_tables(document)
                for key, spec in table.items()
                if _dependency_package(key, spec) == TRYBUILD_PACKAGE
            }
        )
    return aliases


def _owning_aliases(
    relative: pathlib.PurePosixPath,
    aliases: typ.Mapping[pathlib.PurePosixPath, frozenset[str]],
) -> frozenset[str]:
    """Return the trybuild aliases in scope for one source file.

    The nearest manifest at or above the file governs it, exactly as Cargo
    decides which package a target belongs to, so one package's rename cannot
    be read into another package's source. A file no manifest governs matches
    the plain name alone, which is the one reading that needs no manifest.

    Returns
    -------
    frozenset[str]
        The local names trybuild is in scope under.
    """
    for parent in relative.parents:
        found = aliases.get(parent)
        if found is not None:
            return found
    return frozenset({TRYBUILD_PACKAGE})


def _trybuild_facts(
    source: RustSource, scope: TestScope, literal: re.Pattern[str]
) -> list[TrybuildFact]:
    """Return the trybuild source references in one file.

    Parameters
    ----------
    source:
        The decoded file.
    scope:
        The offsets in it that count as test code.
    literal:
        The pattern matching a reference through any alias in scope here.

    Returns
    -------
    list[TrybuildFact]
        One fact per test-scope `TestCases` reference reached by an alias.
    """
    return [
        {
            "path": source.relative.as_posix(),
            "line": line_of(source.text, match.start()),
            "form": "source",
        }
        for match in literal.finditer(source.masked)
        if scope.contains(match.start())
    ]


def fixture_directories(
    checkout: pathlib.Path, manifests: typ.Mapping[pathlib.Path, Manifest]
) -> frozenset[str]:
    """Return the directories that hold a nested-build fixture package.

    A directory qualifies when it is named like a fixture *and* Cargo can build
    it as a root of its own. A `tests/` directory with no manifest is ordinary
    test code, and a package outside the workspace's reach is not a fixture this
    audit can see at all.

    Returns
    -------
    frozenset[str]
        Root-relative POSIX directories of the fixture packages.
    """
    return frozenset(
        directory
        for directory in standalone_directories(checkout, manifests)
        if is_fixture_directory(directory)
    )


def collect_trybuild_manifests(
    checkout: pathlib.Path,
    manifests: typ.Mapping[pathlib.Path, Manifest],
    fixtures: frozenset[str],
) -> list[TrybuildFact]:
    """Return the non-fixture manifests that declare a trybuild dependency.

    A dependency may rename the package it pulls in, so the key is not always
    the package name. Each entry is resolved through its own `package` field
    before the comparison; reading only the keys misses
    `tb = { package = "trybuild", version = "1" }`.

    Returns
    -------
    list[TrybuildFact]
        One fact per manifest whose dependency tables name trybuild.
    """
    found: list[TrybuildFact] = []
    for path, document in sorted(manifests.items()):
        relative = pathlib.PurePosixPath(path.relative_to(checkout).as_posix())
        if in_fixture(relative, fixtures):
            continue
        if any(
            _dependency_package(key, spec) == TRYBUILD_PACKAGE
            for _section, _target, table in cargo_tables(document)
            for key, spec in table.items()
        ):
            found.append({"path": relative.as_posix(), "line": 1, "form": "manifest"})
    return found


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


def _discover_scope(
    checkout: pathlib.Path,
    fixtures: frozenset[str],
    sources: list[RustSource],
    problems: list[str],
) -> dict[pathlib.PurePosixPath, TestScope]:
    """Return the scope of every file the checkout's compiler builds as test code.

    A file's scope and reach are both inputs to its own scan: a declaration
    pulled into test scope is test code throughout, and a file reached by
    `#[path]` resolves its own declarations beside it rather than under a stem
    directory. Each of those answers can arrive after the file was first read,
    so the scan repeats until neither moves — and a declaration that names a
    file nothing declares in an early pass is only reported once the pass that
    settles its owner has run.

    Files are appended to *sources* as declarations reach them, so the caller
    keeps the complete set to report from.

    Returns
    -------
    dict[pathlib.PurePosixPath, TestScope]
        The scope of every source read, keyed by its root-relative path.
    """
    scopes = {
        source.relative: TestScope(whole_file_scope(source.relative))
        for source in sources
    }
    reaches: dict[pathlib.PurePosixPath, Reach] = {}
    by_relative = {source.relative: source for source in sources}
    # The scope and reach each file was scanned under. A file's declarations
    # resolve differently once either moves, so the pair is what decides
    # whether an earlier scan is still current.
    scanned: dict[pathlib.PurePosixPath, tuple[bool, Reach]] = {}
    # A declaration can name no file in an early pass and resolve once the
    # reach of its own file is known, so each scan's findings replace the last
    # and only the final set for each file is reported.
    reported: dict[pathlib.PurePosixPath, list[str]] = {}
    # A file's `mod` declarations only bind if the compiler builds the file.
    # Ordinary library code is scanned so its `#[cfg(test)]` items are found.
    # A whole-file test source is scanned only when Cargo builds it as a
    # target: the rest are text that no compiler reaches — a trybuild case, or
    # a file another test `include!`s — and their `mod` lines name files that
    # nothing declares, which would invent a finding rather than report one.
    pending = [
        source
        for source in sources
        if not scopes[source.relative].whole_file or is_crate_root(source.relative)
    ]
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
    return scopes


def collect_rust_evidence(
    checkout: pathlib.Path,
    manifests: typ.Mapping[pathlib.Path, Manifest],
    problems: list[str],
) -> RustEvidence:
    """Return the spawn and trybuild evidence for one checkout.

    Parameters
    ----------
    checkout:
        The repository root.
    manifests:
        Each decoded `Cargo.toml` below and at the root, by absolute path.
    problems:
        The fact list; an unreadable source appends one sentence each.

    Returns
    -------
    RustEvidence
        The reportable spawns and the trybuild evidence, in path order.
    """
    fixtures = fixture_directories(checkout, manifests)
    sources = read_sources(checkout, fixtures, problems)
    scopes = _discover_scope(checkout, fixtures, sources, problems)
    spawns = [
        fact
        for source in sources
        for fact in _source_facts(source, scopes[source.relative])
    ]
    aliases = _aliases_by_directory(checkout, manifests)
    trybuild = [
        *collect_trybuild_manifests(checkout, manifests, fixtures),
        *(
            fact
            for source in sources
            for fact in _trybuild_facts(
                source,
                scopes[source.relative],
                _test_literal(_owning_aliases(source.relative, aliases)),
            )
        ),
    ]
    return RustEvidence(
        spawns=sorted(spawns, key=operator.itemgetter("path", "line")),
        trybuild=sorted(
            trybuild,
            key=operator.itemgetter("path", "line", "form"),
        ),
    )
