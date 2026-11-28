"""Build the facts for the `nested-cargo-test-builds` rule (NC-011 to NC-013).

The other nested-cargo rules judge how a test's nested build behaves: offline,
into a warm target, inside a deadline. This one asks why the test spends a real
compile at all. Its envelope carries three kinds of evidence:

- the *builders*: every function in test scope that owns a compiler spawn, a
  trybuild or macrotest suite, or a `cargo_metadata` query, with whether the
  reader could see what it spawns and whether it has the shape of a harness test
  (it writes a scratch crate and builds it, and names the repository's own
  harness code);
- the repository's *declarations*: the entries of `.concordat/nested-builds.toml`,
  each with the subject the build is the test of and the warm-up that pays for
  it, and whether that warm-up resolves to something that runs before the tests;
- the *problems*: everything the reader could not establish, so an unreadable
  repository is never reported clean.

Nothing here decides whether a finding is reported. The subject vocabulary, the
severity and the verdict belong to the policy.
"""

from __future__ import annotations

import dataclasses
import operator
import pathlib
import re
import tomllib
import typing as typ

from .fs_probe import probe_file
from .nested_cargo import load_manifests, origin_slug
from .nested_cargo_bindings import function_name, function_text
from .nested_cargo_compiles import COMPILES_NO
from .nested_cargo_makefile_lines import rules_by_target
from .nested_cargo_makefile_preparation import read_makefile
from .nested_cargo_scripts import TEST_TARGET
from .nested_cargo_setup_scripts import covers, read_setup_rules
from .nested_cargo_source import RustSource, read_source, read_sources, whole_file_scope
from .nested_cargo_spawns import collect_rust_evidence, fixture_directories
from .rust_sources import has_rust_sources

if typ.TYPE_CHECKING:
    import collections.abc as cabc

    from .codescene_coverage_envelope import Repository
    from .nested_cargo_setup_scripts import SetupRule

ENVELOPE_SCHEMA_VERSION: typ.Final = 1
ENVELOPE_KIND: typ.Final = "policy-input/nested-cargo-test-builds"
DECLARATIONS: typ.Final = ".concordat/nested-builds.toml"

COVERAGE_RESOLVED: typ.Final = "resolved"
COVERAGE_UNRESOLVED: typ.Final = "unresolved"
COVERAGE_UNREADABLE: typ.Final = "unreadable"

_METADATA: typ.Final = re.compile(r"\bMetadataCommand\b")
_SCRATCH_MANIFEST: typ.Final = re.compile(r"Cargo\.toml|\[package\]")
_TEMPORARY: typ.Final = re.compile(r"\b(?:tempdir|tempdir_in|TempDir|tempfile)\b")
_MAKE_CALL: typ.Final = re.compile(r"(?:\$\(MAKE\)|\bmake)\s+(?P<goal>[\w.-]+)")
_SETUP: typ.Final = "nextest-setup"
_MAKE_PREFIX: typ.Final = "make:"
_TEST_PREFIX: typ.Final = "test:"
_TEST_DIRECTORY_FILE: typ.Final = re.compile(r"(?:^|.*/)tests/(?P<stem>[\w-]+)\.rs$")


class BuilderFact(typ.TypedDict):
    """Store one function in test scope that owns a real compile.

    Attributes
    ----------
    path:
        The source file's path relative to the checkout, in POSIX form.
    line:
        The first line of the builder's first spawn or reference.
    function:
        The enclosing function's name; empty for a trybuild or macrotest suite,
        which a declaration names by file.
    form:
        `spawn`, `trybuild`, `macrotest` or `metadata`.
    readable:
        False when the reader could not see what the spawn starts (a program or
        a subcommand it cannot read), so it can neither accept nor accuse it.
    scratch_crate:
        True when the function writes a manifest and a temporary directory, the
        shape of a test that builds a crate it made up.
    harness_references:
        The configured harness names the function mentions, in configured order.
    """

    path: str
    line: int
    function: str
    form: str
    readable: bool
    scratch_crate: bool
    harness_references: list[str]


class DeclarationFact(typ.TypedDict):
    """Store one entry of the repository's declaration file.

    Attributes
    ----------
    path:
        The file the declaration names, relative to the checkout.
    function:
        The function it names, or empty for every builder in the file.
    subject:
        What the build is the test of, as the file wrote it.
    covered_by:
        The warm-up that pays for the build, as the file wrote it.
    coverage:
        `resolved` when the warm-up exists and runs before the tests,
        `unresolved` when it does not, `unreadable` when the reader could not
        tell (an unknown form, or a Makefile it cannot follow).
    """

    path: str
    function: str
    subject: str
    covered_by: str
    coverage: str


class NestedCargoBuildsEnvelope(typ.TypedDict):
    """Store the evidence evaluated by the nested-cargo test-builds rule.

    Attributes
    ----------
    schema_version:
        The envelope's schema version, which the policy checks.
    kind:
        The policy-input identifier, which the policy checks.
    repository:
        The checkout's path and, where the origin names one, its GitHub slug.
    declarations_state:
        `absent`, `read` or `invalid`, for the declaration file.
    builders:
        Every function in test scope that owns a real compile.
    declarations:
        Every well-formed declaration, with whether its warm-up resolves.
    problems:
        Every fact the builder could not establish, as sentences.
    """

    schema_version: int
    kind: str
    repository: Repository
    declarations_state: str
    builders: list[BuilderFact]
    declarations: list[DeclarationFact]
    problems: list[str]


@dataclasses.dataclass(frozen=True, slots=True)
class _Reading:
    """Hold the declaration file's decoded entries and its state."""

    state: str
    entries: list[dict[str, object]]


def _harness_names(parameters: cabc.Mapping[str, object] | None) -> list[str]:
    """Return the harness names the rule is configured with.

    Returns
    -------
    list[str]
        The non-blank string entries of `harness_paths`, or none.
    """
    configured = (parameters or {}).get("harness_paths", [])
    if not isinstance(configured, list):
        return []
    return [name for name in configured if isinstance(name, str) and name.strip()]


def _offset_of_line(text: str, line: int) -> int:
    """Return the character offset at which 1-based *line* starts."""
    return len("".join(text.splitlines(keepends=True)[: line - 1]))


def _is_scratch_crate(body: str) -> bool:
    """Report whether a function writes a manifest and a temporary directory."""
    return bool(_SCRATCH_MANIFEST.search(body)) and bool(_TEMPORARY.search(body))


def _references(body: str, names: list[str]) -> list[str]:
    """Return the *names* the function body mentions as whole words."""
    return [name for name in names if re.search(rf"\b{re.escape(name)}\b", body)]


def _source_at(
    checkout: pathlib.Path,
    relative: str,
    cache: dict[str, RustSource | None],
    problems: list[str],
) -> RustSource | None:
    """Return the decoded source at *relative*, reading it once.

    Returns
    -------
    RustSource | None
        The source, or None with the reason added to *problems*.
    """
    if relative not in cache:
        source, error = read_source(checkout, pathlib.PurePosixPath(relative))
        if error is not None:
            problems.append(error)
        cache[relative] = source
    return cache[relative]


def _builder(
    source: RustSource,
    line: int,
    form: str,
    names: list[str],
    *,
    readable: bool,
) -> BuilderFact:
    """Return the builder fact for one spawn or reference at *line*.

    Returns
    -------
    BuilderFact
        The fact, with the function it sits in and its harness shape.
    """
    offset = _offset_of_line(source.text, line)
    suite = form in {"trybuild", "macrotest"}
    body = function_text(source, offset)
    return {
        "path": source.relative.as_posix(),
        "line": line,
        "function": "" if suite else function_name(source, offset),
        "form": form,
        "readable": readable,
        "scratch_crate": _is_scratch_crate(body),
        "harness_references": _references(body, names),
    }


def _metadata_lines(source: RustSource) -> list[int]:
    """Return the lines of a `MetadataCommand` use in a whole-file test source."""
    if not whole_file_scope(source.relative):
        return []
    return [
        source.masked.count("\n", 0, match.start()) + 1
        for match in _METADATA.finditer(source.masked)
    ]


def _merged(builders: list[BuilderFact]) -> list[BuilderFact]:
    """Return one fact per builder, keeping the first line and any doubt.

    Returns
    -------
    list[BuilderFact]
        The facts keyed by file, function and form, in path and line order.
    """
    merged: dict[tuple[str, str, str], BuilderFact] = {}
    for fact in builders:
        key = (fact["path"], fact["function"], fact["form"])
        known = merged.get(key)
        if known is None:
            merged[key] = fact
        else:
            known["line"] = min(known["line"], fact["line"])
            known["readable"] = known["readable"] and fact["readable"]
    return sorted(merged.values(), key=operator.itemgetter("path", "line", "form"))


def collect_builders(
    checkout: pathlib.Path,
    manifests: cabc.Mapping[pathlib.Path, typ.Any],
    names: list[str],
    problems: list[str],
) -> list[BuilderFact]:
    """Return every builder in the checkout's test scope.

    Returns
    -------
    list[BuilderFact]
        One fact per distinct file, function and form.
    """
    evidence = collect_rust_evidence(checkout, manifests, problems, cold_build=True)
    fixtures = fixture_directories(checkout, manifests)
    cache: dict[str, RustSource | None] = {
        source.relative.as_posix(): source
        for source in read_sources(checkout, fixtures, [])
    }
    found: list[BuilderFact] = []
    for spawn in evidence.spawns:
        if spawn["compiles"] == COMPILES_NO:
            continue
        source = _source_at(checkout, spawn["path"], cache, problems)
        if source is not None:
            found.append(
                _builder(
                    source,
                    spawn["line"],
                    "spawn",
                    names,
                    readable=spawn["compiles"] != "unknown"
                    and spawn["state"] != "unresolved",
                )
            )
    for form, facts in (
        ("trybuild", [f for f in evidence.trybuild if f["form"] == "source"]),
        ("macrotest", evidence.macrotest),
    ):
        for fact in facts:
            source = _source_at(checkout, fact["path"], cache, problems)
            if source is not None:
                found.append(_builder(source, fact["line"], form, names, readable=True))
    for source in [s for s in cache.values() if s is not None]:
        found.extend(
            _builder(source, line, "metadata", names, readable=True)
            for line in _metadata_lines(source)
        )
    return _merged(found)


def _read_declarations(checkout: pathlib.Path, problems: list[str]) -> _Reading:
    """Read the declaration file's `builder` entries.

    Returns
    -------
    _Reading
        `absent` for no file, `invalid` when it cannot be decoded (the reason is
        in *problems*), otherwise `read` with the entries it holds.
    """
    path = checkout / DECLARATIONS
    probe = probe_file(path)
    if probe.read_error is not None:
        problems.append(f"{DECLARATIONS}: {probe.read_error}")
        return _Reading("invalid", [])
    if not probe.present:
        return _Reading("absent", [])
    try:
        document = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        problems.append(f"{DECLARATIONS}: {error}")
        return _Reading("invalid", [])
    entries = document.get("builder", [])
    if not isinstance(entries, list) or not all(isinstance(e, dict) for e in entries):
        problems.append(f"{DECLARATIONS}: `builder` must be an array of tables")
        return _Reading("invalid", [])
    return _Reading("read", typ.cast("list[dict[str, object]]", entries))


@dataclasses.dataclass(frozen=True, slots=True)
class _Warmups:
    """Hold what the repository offers as warm-ups, read once."""

    setup_rules: list[SetupRule]
    make_goals: frozenset[str] | None
    test_names: frozenset[str]


def _make_goals(checkout: pathlib.Path) -> frozenset[str] | None:
    """Return the Makefile goals that run before or inside `test`.

    A prerequisite of `test` (transitively) and a goal the `test` recipe calls
    through `make` both count: the first is ordered by Make, the second by the
    recipe line. A prerequisite written with a variable reference is not
    followed, because the reader cannot say what it names.

    Returns
    -------
    frozenset[str] | None
        The goal names, or None when there is no Makefile the reader can parse.
    """
    read = read_makefile(checkout)
    if read is None:
        return None
    report, _text = read
    index = rules_by_target(report)
    goals: set[str] = set()
    pending = [TEST_TARGET]
    walked: set[str] = set()
    while pending:
        target = pending.pop()
        if target in walked:
            continue
        walked.add(target)
        for rule in index.get(target, []):
            for prerequisite in rule["prerequisites"]:
                if "$" not in prerequisite:
                    goals.add(prerequisite)
                    pending.append(prerequisite)
            for recipe in rule["recipes"]:
                for call in _MAKE_CALL.finditer(recipe["text"]):
                    goals.add(call.group("goal"))
                    pending.append(call.group("goal"))
    goals.discard(TEST_TARGET)
    return frozenset(goals)


def _test_names(sources: cabc.Iterable[RustSource]) -> frozenset[str]:
    """Return the names of every function a Rust source declares."""
    pattern = re.compile(r"\bfn\s+(?P<name>[A-Za-z_]\w*)")
    return frozenset(
        match.group("name")
        for source in sources
        for match in pattern.finditer(source.masked)
    )


def _coverage(entry: dict[str, str], warmups: _Warmups) -> str:
    """Say whether one declaration's warm-up resolves and runs before the tests.

    Returns
    -------
    str
        `resolved`, `unresolved` or `unreadable`.
    """
    covered_by = entry["covered_by"]
    if covered_by == _SETUP:
        match = _TEST_DIRECTORY_FILE.match(entry["path"])
        binary = match.group("stem") if match else None
        return (
            COVERAGE_RESOLVED
            if covers(warmups.setup_rules, binary, None)
            else COVERAGE_UNRESOLVED
        )
    if covered_by.startswith(_MAKE_PREFIX):
        if warmups.make_goals is None:
            return COVERAGE_UNREADABLE
        goal = covered_by.removeprefix(_MAKE_PREFIX)
        return COVERAGE_RESOLVED if goal in warmups.make_goals else COVERAGE_UNRESOLVED
    if covered_by.startswith(_TEST_PREFIX):
        name = covered_by.removeprefix(_TEST_PREFIX)
        return COVERAGE_RESOLVED if name in warmups.test_names else COVERAGE_UNRESOLVED
    return COVERAGE_UNREADABLE


def _declaration(
    raw: dict[str, object], warmups: _Warmups, problems: list[str], index: int
) -> DeclarationFact | None:
    """Return one declaration as a fact, or None when it is malformed.

    Returns
    -------
    DeclarationFact | None
        The fact; None when `path`, `subject` or `covered_by` is missing or not
        a non-blank string, or `function` is present and not a string. The
        reason is added to *problems*.
    """
    required = ("path", "subject", "covered_by")
    bad = [
        key
        for key in required
        if not isinstance(raw.get(key), str) or not str(raw[key]).strip()
    ]
    function = raw.get("function", "")
    if not isinstance(function, str):
        bad.append("function")
    if bad:
        problems.append(
            f"{DECLARATIONS}: builder entry {index} has a missing or non-string "
            f"field: {', '.join(bad)}"
        )
        return None
    entry = {key: str(raw[key]) for key in required}
    return {
        "path": entry["path"],
        "function": str(function),
        "subject": entry["subject"],
        "covered_by": entry["covered_by"],
        "coverage": _coverage(entry, warmups),
    }


def build_nested_cargo_builds_envelope(
    checkout: pathlib.Path, parameters: cabc.Mapping[str, object] | None = None
) -> NestedCargoBuildsEnvelope:
    """Assemble the test-builds evidence for the nested-cargo rule.

    Parameters
    ----------
    checkout:
        The repository checkout to describe.
    parameters:
        The rule's configured parameters. `harness_paths` is read here, because
        it decides which names a builder is recorded as mentioning.

    Returns
    -------
    NestedCargoBuildsEnvelope
        The builders, the declarations and every fact that could not be read.
    """
    problems: list[str] = []
    manifests = load_manifests(checkout, problems)
    repository: Repository = {"path": ".", "name": origin_slug(checkout)}
    if not manifests and not problems and not has_rust_sources(checkout):
        return {
            "schema_version": ENVELOPE_SCHEMA_VERSION,
            "kind": ENVELOPE_KIND,
            "repository": repository,
            "declarations_state": "absent",
            "builders": [],
            "declarations": [],
            "problems": [],
        }
    builders = collect_builders(
        checkout, manifests, _harness_names(parameters), problems
    )
    reading = _read_declarations(checkout, problems)
    warmups = _Warmups(
        read_setup_rules(checkout, problems),
        _make_goals(checkout),
        _test_names(
            read_sources(checkout, fixture_directories(checkout, manifests), [])
        ),
    )
    declarations = [
        fact
        for index, raw in enumerate(reading.entries, start=1)
        if (fact := _declaration(raw, warmups, problems, index)) is not None
    ]
    return {
        "schema_version": ENVELOPE_SCHEMA_VERSION,
        "kind": ENVELOPE_KIND,
        "repository": repository,
        "declarations_state": reading.state,
        "builders": builders,
        "declarations": declarations,
        "problems": sorted(set(problems)),
    }
