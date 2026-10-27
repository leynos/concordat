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
span of that item, and a `#[cfg(test)] mod name;` is resolved to the sibling
file it names. A fixture package's sources are nested-build input rather than
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
)
from .nested_cargo_modules import resolve_declarations, scan_scope
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


CRATE_ROOT_STEMS: typ.Final = frozenset({"lib", "main", "mod"})

_ATTRIBUTE: typ.Final = re.compile(r"#\[")
_HELPER: typ.Final = re.compile(r"\bfn\s+(?P<name>[A-Za-z_]\w*)\s*\(")
_MODULE: typ.Final = re.compile(r"mod\s+(?P<name>[A-Za-z_]\w*)")
_MODIFIERS: typ.Final = re.compile(r"(?:pub(?:\s*\([^)]*\))?\s+)*")
_ENCLOSING_MODULE: typ.Final = re.compile(r"\bmod\s+([A-Za-z_]\w*)\s*$")
_TEST_LITERAL: typ.Final = re.compile(r"trybuild\s*::\s*TestCases")
_DECLARATION: typ.Final = re.compile(r"\bfn\s*$")
_CFG_PREDICATE: typ.Final = re.compile(r"cfg\s*\((.*)\)\s*$", re.DOTALL)
_PATH_ATTRIBUTE: typ.Final = re.compile(r"\bpath\s*=\s*\"[^\"]*\"")


@dataclasses.dataclass(frozen=True, slots=True)
class _Declaration:
    """One `#[cfg(test)] mod name;` declaration and where Rust looks for it.

    Attributes
    ----------
    chain:
        The inline modules enclosing the declaration, outermost first.
    name:
        The module name, which names the file when no `path` is given.
    path:
        The literal `#[path = "..."]` override, if the declaration has one.
    """

    chain: tuple[str, ...]
    name: str
    path: str | None


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
        sites.extend(
            site
            for site in _helper_sites(source, name, body, pairs)
            if scope.contains(site.start) and not _absorbed(site, definition)
        )
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


def _trybuild_facts(source: RustSource, scope: TestScope) -> list[TrybuildFact]:
    """Return the trybuild source references in one file.

    Returns
    -------
    list[TrybuildFact]
        One fact per test-scope `trybuild::TestCases` reference.
    """
    return [
        {
            "path": source.relative.as_posix(),
            "line": line_of(source.text, match.start()),
            "form": "source",
        }
        for match in _TEST_LITERAL.finditer(source.masked)
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
            "trybuild" in table for _section, _target, table in cargo_tables(document)
        ):
            found.append({"path": relative.as_posix(), "line": 1, "form": "manifest"})
    return found


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
    scopes = {
        source.relative: TestScope(whole_file_scope(source.relative))
        for source in sources
    }
    pending = [source for source in sources if not scopes[source.relative].whole_file]
    while pending:
        source = pending.pop()
        scope, declarations = scan_scope(source, problems)
        known = scopes.get(source.relative)
        if known is not None and known.whole_file:
            # A module declaration already proved this whole file is test code.
            # Rescanning it must not overwrite that stronger claim with the
            # narrower attribute ranges this pass found.
            known.ranges = scope.ranges
        else:
            scopes[source.relative] = scope
        for relative in resolve_declarations(
            checkout, source, declarations, scopes, fixtures, problems
        ):
            found, error = read_source(checkout, relative)
            if error is not None:
                problems.append(error)
            if found is not None:
                sources.append(found)
                pending.append(found)
    spawns = [
        fact
        for source in sources
        for fact in _source_facts(source, scopes[source.relative])
    ]
    trybuild = [
        *collect_trybuild_manifests(checkout, manifests, fixtures),
        *(
            fact
            for source in sources
            for fact in _trybuild_facts(source, scopes[source.relative])
        ),
    ]
    return RustEvidence(
        spawns=sorted(spawns, key=operator.itemgetter("path", "line")),
        trybuild=sorted(
            trybuild,
            key=operator.itemgetter("path", "line", "form"),
        ),
    )
