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
# A command whose subcommand the reader cannot see, until the crate it belongs
# to is shown to compile elsewhere.
MAYBE_KIND: typ.Final = "cargo-unknown"
_FUNCTION: typ.Final = re.compile(r"\bfn\s+(?P<name>[A-Za-z_]\w*)")
_BINARY: typ.Final = re.compile(r"(?:^|/)tests/(?P<stem>[^/]+)\.rs$")
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
    prepared_by:
        Which of `setup-script`, `preparation-test` and `gate-call` apply.
        Empty means the repository declares no preparation for this source.
    """

    path: str
    line: int
    kind: str
    binary: str | None
    crate: str
    prepared_by: list[str]


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
    """

    crates: cabc.Mapping[str, str | None]
    rules: list[SetupRule]
    gates: Gates


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
        if covers(preparation.rules, stem, crates.get(crate)):
            prepared.insert(0, SETUP_SCRIPT)
        builds.append({
            "path": path,
            "line": line,
            "kind": "cargo" if kind == MAYBE_KIND and crate in compiling else kind,
            "binary": stem,
            "crate": crate,
            "prepared_by": prepared,
        })
    return builds
