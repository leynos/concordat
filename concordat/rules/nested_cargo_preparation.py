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
import tomllib
import typing as typ

from .dependency_family_coverage import mapping, sequence
from .fs_probe import probe_file
from .nested_cargo_spawn import COMPILES_NO, COMPILES_YES, TEST_PATH

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    import pathlib

    from .nested_cargo import Manifest
    from .nested_cargo_source import RustSource
    from .nested_cargo_spawns import RustEvidence

NEXTEST_CONFIG: typ.Final = ".config/nextest.toml"
SETUP_SCRIPT: typ.Final = "setup-script"
PREPARATION_TEST: typ.Final = "preparation-test"
GATE_CALL: typ.Final = "gate-call"
ALL_FILTER: typ.Final = "all()"
# A command whose subcommand the reader cannot see, until the crate it belongs
# to is shown to compile elsewhere.
MAYBE_KIND: typ.Final = "cargo-unknown"
_FUNCTION: typ.Final = re.compile(r"\bfn\s+(?P<name>[A-Za-z_]\w*)")
_BINARY: typ.Final = re.compile(r"(?:^|/)tests/(?P<stem>[^/]+)\.rs$")


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


@dataclasses.dataclass(frozen=True, slots=True)
class SetupRule:
    """Store one nextest rule that runs a defined setup script.

    Attributes
    ----------
    filter:
        The rule's filterset expression; a missing one selects every test.
    """

    filter: str


def _strings(value: object) -> tuple[str, ...]:
    """Return the string items of a configured list, ignoring anything else."""
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, str) and item)


def read_gates(parameters: cabc.Mapping[str, object] | None) -> Gates:
    """Return the preparation names the rule's parameters configure.

    A pattern that is not a valid regular expression is skipped: the rule would
    otherwise refuse to run, and the policy reports nothing it cannot read.

    Returns
    -------
    Gates
        The configured function names, test patterns and compiling helpers.
    """
    configured = (parameters or {}).get("preparation_gates")
    block = configured if isinstance(configured, dict) else {}
    patterns: list[re.Pattern[str]] = []
    for text in _strings(block.get("tests")):
        try:
            patterns.append(re.compile(text))
        except re.error:
            continue
    return Gates(
        functions=_strings(block.get("functions")),
        tests=tuple(patterns),
        compiling_calls=_strings((parameters or {}).get("compiling_calls")),
    )


def _defined_scripts(document: dict[str, object]) -> set[str]:
    """Return the names of the setup scripts a nextest configuration defines."""
    scripts = mapping(document.get("scripts")) or {}
    return set(mapping(scripts.get("setup")) or {})


def _profile_rules(document: dict[str, object]) -> list[dict[str, object]]:
    """Return every `[[profile.<name>.scripts]]` rule of every profile."""
    rules: list[dict[str, object]] = []
    for profile in (mapping(document.get("profile")) or {}).values():
        found = sequence((mapping(profile) or {}).get("scripts")) or []
        rules.extend(rule for item in found if (rule := mapping(item)) is not None)
    return rules


def read_setup_rules(checkout: pathlib.Path, problems: list[str]) -> list[SetupRule]:
    """Return the nextest rules that run a setup script the file defines.

    A rule naming a script nothing defines is a configuration nextest itself
    refuses, so it prepares nothing and is left out.

    Returns
    -------
    list[SetupRule]
        One entry per rule, empty when there is no readable configuration.
    """
    path = checkout / NEXTEST_CONFIG
    probe = probe_file(path)
    if probe.read_error is not None:
        problems.append(f"{NEXTEST_CONFIG}: {probe.read_error}")
        return []
    if not probe.present:
        return []
    try:
        document = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        problems.append(f"cannot read {NEXTEST_CONFIG}: {error}")
        return []
    defined = _defined_scripts(document)
    rules: list[SetupRule] = []
    for rule in _profile_rules(document):
        names = rule.get("setup")
        listed = [names] if isinstance(names, str) else _strings(names)
        if listed and all(name in defined for name in listed):
            selection = rule.get("filter")
            rules.append(
                SetupRule(selection if isinstance(selection, str) else ALL_FILTER)
            )
    return rules


def _mentions(filterset: str, name: str) -> bool:
    """Report whether *name* occurs in *filterset* as a whole word."""
    return re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", filterset) is not None


def _covered(rules: list[SetupRule], names: list[str]) -> bool:
    """Report whether a setup rule selects every test or names one of *names*."""
    return any(
        rule.filter.strip() == ALL_FILTER
        or any(_mentions(rule.filter, name) for name in names)
        for rule in rules
    )


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


def _declared_by_crate(
    sources: list[RustSource],
    crates: cabc.Mapping[str, str | None],
    gates: Gates,
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
        relative = source.relative.as_posix()
        crate = owning_crate(relative, crates)
        found = declared.setdefault(crate, set())
        if any(
            pattern.search(match["name"])
            for match in _FUNCTION.finditer(source.masked)
            for pattern in gates.tests
        ):
            found.add(PREPARATION_TEST)
        if any(_calls(source, name) for name in gates.functions):
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
        if not TEST_PATH.search(relative):
            continue
        calls = [
            match
            for name in gates.compiling_calls
            if (match := _calls(source, name)) is not None
        ]
        if calls:
            first = min(match.start() for match in calls)
            found.append((relative, source.text.count("\n", 0, first) + 1))
    return found


def _first_compiles(
    evidence: RustEvidence, sources: list[RustSource], gates: Gates
) -> dict[tuple[str, str], int]:
    """Return the first line each file starts a build of each kind on.

    Returns
    -------
    dict[tuple[str, str], int]
        The line, keyed by `(path, kind)`, where kind is `cargo`, the
        `cargo-unknown` of an unseen subcommand, `trybuild`, `macrotest` or
        `helper`.
    """
    first: dict[tuple[str, str], int] = {}

    def note(path: str, line: int, kind: str) -> None:
        """Keep the earliest line a file starts a build of this kind on."""
        key = (path, kind)
        first[key] = min(line, first.get(key, line))

    for spawn in evidence.spawns:
        if spawn["compiles"] == COMPILES_YES:
            note(spawn["path"], spawn["line"], "cargo")
        elif spawn["compiles"] != COMPILES_NO:
            note(spawn["path"], spawn["line"], MAYBE_KIND)
    for fact in evidence.trybuild:
        if fact["form"] == "source":
            note(fact["path"], fact["line"], "trybuild")
    for fact in evidence.macrotest:
        note(fact["path"], fact["line"], "macrotest")
    for path, line in _helper_facts(sources, gates):
        note(path, line, "helper")
    return first


def collect_builds(
    evidence: RustEvidence,
    sources: list[RustSource],
    crates: cabc.Mapping[str, str | None],
    rules: list[SetupRule],
    gates: Gates,
) -> list[BuildFact]:
    """Return every test source that compiles in a timed test, and its preparation.

    Parameters
    ----------
    evidence:
        The spawn, trybuild and macrotest facts of the checkout.
    sources:
        The decoded Rust sources, which carry the gate and preparation names.
    crates:
        Crate directories with their package names.
    rules:
        The nextest rules that run a defined setup script.
    gates:
        The configured preparation and compiling-helper names.

    Returns
    -------
    list[BuildFact]
        One fact per path and kind, in path order.
    """
    declared = _declared_by_crate(sources, crates, gates)
    first = _first_compiles(evidence, sources, gates)
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
        settled = "cargo" if kind == MAYBE_KIND and crate in compiling else kind
        binary = _BINARY.search(path)
        stem = binary["stem"] if binary else None
        prepared = sorted(declared.get(crate, set()))
        names = [name for name in (stem, crates.get(crate)) if name]
        if _covered(rules, names):
            prepared.insert(0, SETUP_SCRIPT)
        builds.append({
            "path": path,
            "line": line,
            "kind": settled,
            "binary": stem,
            "crate": crate,
            "prepared_by": prepared,
        })
    return builds
