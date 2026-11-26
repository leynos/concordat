"""Find where a checkout pays for a nested build outside its timed tests.

A test that compiles inside its own deadline pays for a cold build when nothing
filled the target directory first, and it then fails for a reason its assertion
never touches. The fix is to prepare outside the timed body, and a repository
can say so in three ways this module reads:

- a nextest setup script (`[scripts.setup.<name>]`) that a profile's
  `[[profile.<p>.scripts]]` rule selects for the test, which nextest itself
  runs before the tests it covers;
- a named preparation test in the same crate, whose name matches one of the
  configured patterns (rstest-bdd's `nested_fixture_preparation` is one);
- a call to a preparation gate in the same crate, a function that refuses a
  timed test whose artefacts are missing or stale (rstest-bdd's
  `ensure_prepared` is one).

None of the three is a claim that the build is warm: the reader cannot know
that. It is a claim that the repository has *declared* where the compile work
is paid for, which is what a maintainer reading a timeout needs to find.
"""

from __future__ import annotations

import dataclasses
import re
import typing as typ

from .nested_cargo_compiles import COMPILES_NO, COMPILES_YES
from .nested_cargo_reach import UNSEEN
from .nested_cargo_setup_scripts import covers
from .nested_cargo_spawn import TEST_PATH

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    import pathlib

    from .nested_cargo import Manifest
    from .nested_cargo_gates import Gates
    from .nested_cargo_setup_scripts import SetupRule
    from .nested_cargo_source import RustSource
    from .nested_cargo_spawns import RustEvidence

SETUP_SCRIPT: typ.Final = "setup-script"
PREPARATION_TEST: typ.Final = "preparation-test"
GATE_CALL: typ.Final = "gate-call"
MAKEFILE_PREPARATION: typ.Final = "makefile-preparation"
# A command whose subcommand the reader cannot see, until the crate it belongs
# to is shown to compile elsewhere.
MAYBE_KIND: typ.Final = "cargo-unknown"
_FUNCTION: typ.Final = re.compile(r"\bfn\s+(?P<name>[A-Za-z_]\w*)")
_BINARY: typ.Final = re.compile(r"(?:^|/)tests/(?P<stem>[^/]+)\.rs$")
# A test target by its Cargo name: `tests/<name>.rs` or `tests/<name>/main.rs`.
_TARGET_NAME: typ.Final = re.compile(
    r"(?:^|/)tests/(?P<name>[^/]+?)(?:\.rs|/main\.rs)$"
)
# `#[test]`, `#[tokio::test]`, `#[rstest]` and `#[test_case(..)]` mark a harness
# test; a bare function of the same name does not.
_TEST_ATTRIBUTE: typ.Final = re.compile(r"#\[\s*(?:\w+::)*(?:test|rstest|test_case)\b")


class BuildFact(typ.TypedDict):
    """Store one test source that compiles in a timed test, and what prepares it.

    Attributes
    ----------
    path:
        The source's path relative to the checkout, in POSIX form.
    line:
        The line the first compile of this kind starts on.
    kind:
        `cargo` for a compiling spawn, or a spawn whose subcommand the reader
        cannot see in a crate that visibly compiles elsewhere; `cargo-unknown`
        for that spawn anywhere else; `trybuild`, `macrotest`, or `helper` for
        a call to a function the repository's own harness builds through.
    binary:
        The test binary's name for a top-level `tests/<stem>.rs`, else None.
    crate:
        The directory of the crate that owns the source, `.` for the root.
    package:
        The package name of the owning crate, or None when it declares none.
    prepared_by:
        Which of `setup-script`, `preparation-test`, `gate-call` and
        `makefile-preparation` apply. Empty means the repository declares no
        preparation for this source.
    targets:
        The names of the test binaries (`tests/<name>.rs`) whose module tree
        compiles the source, sorted: the file's own binary and every one that
        imports it. Empty when only a library reaches it; None when nothing
        was seen reaching it, so no `--test NAME` run can be said to cover it.
    """

    path: str
    line: int
    kind: str
    binary: str | None
    crate: str
    package: str | None
    prepared_by: list[str]
    targets: list[str] | None


@dataclasses.dataclass(frozen=True, slots=True)
class Preparation:
    """Hold what a checkout declares, and the names it declares them by.

    Attributes
    ----------
    crates:
        Crate directories with their package names.
    rules:
        The nextest rules that run a defined setup script.
    gates:
        The configured preparation and compiling-helper names.
    is_makefile_prepared:
        Whether the Makefile's `test` target compiles before it tests, which
        prepares every build the checkout's tests start.
    named_test_crates:
        Crate directories whose manifest names its test targets (`[[test]]`
        tables, or `autotests = false`), so a file's name no longer says which
        `--test NAME` runs it.
    """

    crates: cabc.Mapping[str, str | None]
    rules: list[SetupRule]
    gates: Gates
    is_makefile_prepared: bool = False
    named_test_crates: frozenset[str] = frozenset()
    declared_tests: cabc.Mapping[str, str] = dataclasses.field(default_factory=dict)


def declared_tests(
    checkout: pathlib.Path, manifests: cabc.Mapping[pathlib.Path, Manifest]
) -> dict[str, str]:
    """Return each `[[test]]` table's source path, root-relative, and its name.

    Returns
    -------
    dict[str, str]
        The path (`tests/<name>.rs` when the table gives none) to the target name.
    """
    found: dict[str, str] = {}
    for path, document in manifests.items():
        tables = document.get("test")
        for table in tables if isinstance(tables, list) else []:
            declared = _test_table(table)
            if declared is not None:
                relative = (path.parent / declared[1]).relative_to(checkout)
                found[relative.as_posix()] = declared[0]
    return found


def _test_table(table: object) -> tuple[str, str] | None:
    """Return a `[[test]]` table's name and source path, or None when unnamed."""
    if not isinstance(table, dict):
        return None
    name = table.get("name")
    if not isinstance(name, str):
        return None
    given = table.get("path")
    return name, given if isinstance(given, str) else f"tests/{name}.rs"


def named_test_crates(
    checkout: pathlib.Path, manifests: cabc.Mapping[pathlib.Path, Manifest]
) -> frozenset[str]:
    """Return the crate directories whose manifest names its own test targets.

    Returns
    -------
    frozenset[str]
        Root-relative POSIX directories whose manifest has a `[[test]]` table or
        sets `autotests = false`.
    """
    return frozenset(
        path.parent.relative_to(checkout).as_posix()
        for path, document in manifests.items()
        if _names_tests(document)
    )


def _names_tests(document: Manifest) -> bool:
    """Return whether a manifest declares test targets by name."""
    package = document.get("package")
    is_manual = isinstance(package, dict) and package.get("autotests") is False
    return is_manual or bool(document.get("test"))


def crate_directories(
    checkout: pathlib.Path, manifests: cabc.Mapping[pathlib.Path, Manifest]
) -> dict[str, str | None]:
    """Return each manifest's directory, with the package name it declares.

    Returns
    -------
    dict[str, str | None]
        Root-relative POSIX directory (`.` for the root) to package name.
    """
    found: dict[str, str | None] = {}
    for path, document in manifests.items():
        directory = path.parent.relative_to(checkout).as_posix()
        package = document.get("package")
        name = package.get("name") if isinstance(package, dict) else None
        found[directory] = name if isinstance(name, str) else None
    return found


def owning_crate(relative: str, crates: cabc.Mapping[str, str | None]) -> str:
    """Return the nearest crate directory at or above *relative*.

    Returns
    -------
    str
        The directory, `.` when the root manifest or none governs the file.
    """
    parts = relative.split("/")[:-1]
    for end in range(len(parts), 0, -1):
        candidate = "/".join(parts[:end])
        if candidate in crates:
            return candidate
    return "."


def _calls(source: RustSource, name: str) -> re.Match[str] | None:
    """Return the first call to *name* in *source*, ignoring a definition."""
    for match in re.finditer(
        rf"(?<![\w])(?:fn\s+)?{re.escape(name)}\s*\(", source.masked
    ):
        if not match.group().startswith("fn"):
            return match
    return None


def _is_test_function(masked: str, start: int) -> bool:
    """Report whether the `fn` at *start* carries a test attribute.

    The attributes of a function sit between the end of the item before it (a
    closing brace or a semicolon) and its `fn` keyword, so that stretch is what
    is searched. An ordinary helper that happens to share a preparation test's
    name is not one.

    Returns
    -------
    bool
        True when a test attribute precedes the function.
    """
    boundary = max(masked.rfind("}", 0, start), masked.rfind(";", 0, start))
    return _TEST_ATTRIBUTE.search(masked, boundary + 1, start) is not None


def _declares_preparation_test(source: RustSource, gates: Gates) -> bool:
    """Report whether *source* defines a test a preparation pattern matches."""
    return any(
        _is_test_function(source.masked, match.start())
        for match in _FUNCTION.finditer(source.masked)
        if any(pattern.search(match["name"]) for pattern in gates.tests)
    )


def _declared_by_crate(
    sources: list[RustSource], preparation: Preparation
) -> dict[str, set[str]]:
    """Return the preparation each crate declares in source, by crate directory.

    Returns
    -------
    dict[str, set[str]]
        For each crate, `preparation-test` if a test function matches a
        configured pattern, `gate-call` if a configured gate is called.
    """
    declared: dict[str, set[str]] = {}
    for source in sources:
        crate = owning_crate(source.relative.as_posix(), preparation.crates)
        found = declared.setdefault(crate, set())
        if _declares_preparation_test(source, preparation.gates):
            found.add(PREPARATION_TEST)
        if any(_calls(source, name) for name in preparation.gates.functions):
            found.add(GATE_CALL)
    return declared


def _helper_facts(sources: list[RustSource], gates: Gates) -> list[tuple[str, int]]:
    """Return each test-scope call to a configured compiling helper.

    Only a file under a test directory counts: a helper called from library
    code is not a timed test's own work.

    Returns
    -------
    list[tuple[str, int]]
        The path and line of the first call in each file.
    """
    found: list[tuple[str, int]] = []
    for source in sources:
        relative = source.relative.as_posix()
        calls = [
            match
            for name in gates.compiling_calls
            if (match := _calls(source, name)) is not None
        ]
        if calls and TEST_PATH.search(relative):
            first = min(match.start() for match in calls)
            found.append((relative, source.text.count("\n", 0, first) + 1))
    return found


def _spawn_kind(compiles: str) -> str | None:
    """Return the build kind of a spawn, or None when it compiles nothing."""
    if compiles == COMPILES_YES:
        return "cargo"
    return None if compiles == COMPILES_NO else MAYBE_KIND


def _triggers(
    evidence: RustEvidence, sources: list[RustSource], gates: Gates
) -> list[tuple[str, int, str]]:
    """Return every `(path, line, kind)` at which a test source starts a build.

    Returns
    -------
    list[tuple[str, int, str]]
        One entry per spawn that compiles or may, trybuild source reference,
        macrotest use and call to a configured compiling helper.
    """
    spawns = [
        (spawn["path"], spawn["line"], kind)
        for spawn in evidence.spawns
        if (kind := _spawn_kind(spawn["compiles"])) is not None
    ]
    trybuild = [
        (fact["path"], fact["line"], "trybuild")
        for fact in evidence.trybuild
        if fact["form"] == "source"
    ]
    macrotest = [
        (fact["path"], fact["line"], "macrotest") for fact in evidence.macrotest
    ]
    helpers = [(path, line, "helper") for path, line in _helper_facts(sources, gates)]
    return [*spawns, *trybuild, *macrotest, *helpers]


def _first_lines(
    triggers: list[tuple[str, int, str]],
) -> dict[tuple[str, str], int]:
    """Return the first line each file starts a build of each kind on."""
    first: dict[tuple[str, str], int] = {}
    for path, line, kind in triggers:
        key = (path, kind)
        first[key] = min(line, first.get(key, line))
    return first


def _is_root_covered(root: str, preparation: Preparation) -> bool:
    """Report whether a setup rule runs before the tests built from *root*.

    A `tests/<stem>.rs` root is a test binary of that name; any other root (a
    library, or an owner the resolver did not follow) has no binary name, so
    only a rule that selects every test or its whole package covers it.

    Returns
    -------
    bool
        True when the setup rules cover the root's tests.
    """
    # An unseen owner is named for the file it stands behind, which may be a
    # `tests/<stem>.rs`; it is not that binary, so its name is never read.
    if root.startswith(UNSEEN):
        # Its package is unknown too, so only a rule that selects every test
        # covers it; reading the prefix as a path would pick the root package.
        return covers(preparation.rules, None, None)
    binary = _BINARY.search(root)
    stem = binary["stem"] if binary else None
    package = preparation.crates.get(owning_crate(root, preparation.crates))
    return covers(preparation.rules, stem, package)


def _is_setup_covered(
    path: str,
    stem: str | None,
    reached_by: cabc.Mapping[str, list[str]],
    preparation: Preparation,
) -> bool:
    """Report whether a setup rule runs before the tests that compile *path*.

    A top-level `tests/<stem>.rs` is its own binary, and it is covered only if
    every other target that imports it is covered too. Any other file compiles
    only inside the targets whose `mod` or `#[path]` chain reaches it, so it is
    covered when every one of them is. A file no target reaches, or one a
    library or an uncovered binary also reaches, is not: the setup script then
    does not run before every test that pays for the build.

    Returns
    -------
    bool
        True when the setup rules cover the file and every target that reaches
        it.
    """
    package = preparation.crates.get(owning_crate(path, preparation.crates))
    is_own_covered = covers(preparation.rules, stem, package)
    roots = reached_by.get(path, [])
    are_roots_covered = all(_is_root_covered(root, preparation) for root in roots)
    # A reached file runs in every target that reaches it, so the source
    # package's own rule cannot stand for an importer in another package. A
    # support file nothing reaches may be `include!`d from anywhere, so only a
    # rule that selects every test covers it; a binary stands for itself.
    if roots:
        return are_roots_covered
    return is_own_covered if stem is not None else covers(preparation.rules, None, None)


def collect_builds(
    evidence: RustEvidence, sources: list[RustSource], preparation: Preparation
) -> list[BuildFact]:
    """Return every test source that compiles in a timed test, and its preparation.

    Parameters
    ----------
    evidence:
        The spawn, trybuild and macrotest facts of the checkout.
    sources:
        The decoded Rust sources, which carry the gate and preparation names.
    preparation:
        What the checkout declares: its crates, nextest setup rules and names.

    Returns
    -------
    list[BuildFact]
        One fact per path and kind, in path order.
    """
    crates = preparation.crates
    declared = _declared_by_crate(sources, preparation)
    first = _first_lines(_triggers(evidence, sources, preparation.gates))
    # A command whose subcommand the reader cannot see is a build only where the
    # same crate visibly compiles elsewhere. Anywhere else the arguments are as
    # likely `metadata` or `--unit-graph` as `build`, and calling it a build is
    # the guess this reader does not make; the policy reports it as unknown.
    compiling = {
        owning_crate(path, crates) for path, kind in first if kind != MAYBE_KIND
    }
    builds: list[BuildFact] = []
    for (path, kind), line in sorted(first.items()):
        crate = owning_crate(path, crates)
        binary = _BINARY.search(path)
        stem = binary["stem"] if binary else None
        prepared = sorted(declared.get(crate, set()))
        if _is_setup_covered(path, stem, evidence.reached_by, preparation):
            prepared.insert(0, SETUP_SCRIPT)
        if preparation.is_makefile_prepared:
            prepared.append(MAKEFILE_PREPARATION)
        builds.append({
            "path": path,
            "line": line,
            "kind": "cargo" if kind == MAYBE_KIND and crate in compiling else kind,
            "binary": stem,
            "crate": crate,
            "package": crates.get(crate),
            "prepared_by": prepared,
            "targets": _test_targets(
                path,
                evidence.reached_by.get(path, []),
                preparation.declared_tests
                if crate in preparation.named_test_crates
                else {},
                is_named=crate in preparation.named_test_crates,
            ),
        })
    return builds


def _test_targets(
    path: str,
    roots: list[str],
    declared: cabc.Mapping[str, str],
    *,
    is_named: bool,
) -> list[str] | None:
    """Return the test binaries that compile a source, or None when not all are seen.

    Cargo names a `tests/<name>.rs` file and a `tests/<name>/main.rs` file's
    directory the same target, `<name>`. A `[[test]]` table names its own path's
    target, and a path it does not name stays unknown in a crate whose manifest
    names its tests, because the file name no longer says which target runs it.

    Returns
    -------
    list[str] | None
        The sorted names of the file's own binary and of every test target among
        *roots*; empty when only a library reaches it; None when nothing reaches
        it and it is no binary itself, or an owner the resolver cannot see
        (`unseen:`) also compiles it, so the list could not be complete.
    """
    if any(root.startswith(UNSEEN) for root in roots):
        return None
    sources = _owners(path, roots, declared)
    if is_named and not all(source in declared for source in sources):
        return None
    names = _target_names(sources, declared)
    if not names and not roots:
        return None
    return sorted(names)


def _target_names(sources: list[str], declared: cabc.Mapping[str, str]) -> set[str]:
    """Return the target names of *sources*: a table's name, else the file's."""
    named = {declared[source] for source in sources if source in declared}
    by_file = (
        found["name"]
        for source, found in ((s, _TARGET_NAME.search(s)) for s in sources)
        if found and source not in declared
    )
    return named | set(by_file)


def _owners(path: str, roots: list[str], declared: cabc.Mapping[str, str]) -> list[str]:
    """Return the file and the roots that own it, without guessed roots.

    A `[[test]]` table that gives its own path leaves `tests/<name>.rs` a root
    the resolver guessed from the name, not a file: it is no owner.

    Returns
    -------
    list[str]
        The file's own path, then each root that is a real owner.
    """
    prefix = path[: path.find("tests/")] if "tests/" in path else ""
    guessed = {f"{prefix}tests/{name}.rs" for name in declared.values()}
    return [path, *(r for r in roots if r in declared or r not in guessed)]
