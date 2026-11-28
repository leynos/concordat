"""Find the functions in test scope that own a real compile.

A *builder* is the function that owns a compiler spawn (a `cargo`, `rustc` or
`env!("CARGO")` command), a `cargo_metadata::MetadataCommand` query, or the file
that holds a trybuild or macrotest suite. The repository declares builders, not
call sites, so a helper that many tests call is declared once.

For each builder the reader also records two things the policy needs for NC-012:
whether the function writes a scratch crate (a manifest literal, a write call
and a temporary directory) and which of the configured harness names it uses.
Comments and strings are masked first, so a name in either is not a use.
"""

from __future__ import annotations

import operator
import pathlib
import re
import typing as typ

from .nested_cargo_bindings import function_name, function_span
from .nested_cargo_commands import COMMAND_NEW
from .nested_cargo_compiles import COMPILES_NO, COMPILES_UNKNOWN
from .nested_cargo_source import RustSource, read_source, read_sources, whole_file_scope
from .nested_cargo_spawns import (
    RustEvidence,
    SpawnFact,
    collect_rust_evidence,
    fixture_directories,
)

if typ.TYPE_CHECKING:
    import collections.abc as cabc

_METADATA: typ.Final = re.compile(r"\bMetadataCommand\b")
_MANIFEST_LITERAL: typ.Final = re.compile(
    r'"[^"\n]*(?:Cargo\.toml|\[package\])[^"\n]*"'
)
_WRITE_CALL: typ.Final = re.compile(r"\b(?:write|write_all|create|create_new)\s*\(")
_TEMPORARY: typ.Final = re.compile(r"\b(?:tempdir|tempdir_in|TempDir|tempfile)\b")
_BUILD_LOCATION: typ.Final = re.compile(r"current_dir|manifest[-_]path")
_READ_CALL: typ.Final = re.compile(r"\b(?:read\w*|open|exists)\s*\(")
_STATEMENT: typ.Final = re.compile(r"[^;{}]*;")
_LET_NAME: typ.Final = re.compile(r"\s*let\s+(?:mut\s+)?(?P<name>[A-Za-z_]\w*)")
_WORD: typ.Final = re.compile(r"[A-Za-z_]\w*")
_SUITES: typ.Final = ("trybuild", "macrotest")


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
        True when the function writes a manifest into a temporary directory,
        the shape of a test that builds a crate it made up.
    harness_references:
        The configured harness names the function uses, in configured order.
    """

    path: str
    line: int
    function: str
    form: str
    readable: bool
    scratch_crate: bool
    harness_references: list[str]


class _Sources:
    """Hold the decoded sources of a checkout, reading a missed one once."""

    def __init__(
        self, checkout: pathlib.Path, preloaded: cabc.Iterable[RustSource]
    ) -> None:
        """Start from the sources already decoded for *checkout*."""
        self._checkout = checkout
        self._known: dict[str, RustSource | None] = {
            source.relative.as_posix(): source for source in preloaded
        }

    def get(self, relative: str, problems: list[str]) -> RustSource | None:
        """Return the source at *relative*, or None with the reason in *problems*."""
        if relative not in self._known:
            source, error = read_source(self._checkout, pathlib.PurePosixPath(relative))
            if error is not None:
                problems.append(error)
            self._known[relative] = source
        return self._known[relative]

    def loaded(self) -> list[RustSource]:
        """Return every source read so far."""
        return [source for source in self._known.values() if source is not None]


def _line_span(text: str, line: int) -> tuple[int, int]:
    """Return the `(start, end)` offsets of 1-based *line*, without its newline."""
    lines = text.splitlines(keepends=True)
    start = len("".join(lines[: line - 1]))
    if line > len(lines):
        return start, start
    return start, start + len(lines[line - 1].rstrip("\r\n"))


def _spawn_offset(source: RustSource, line: int) -> int:
    """Return an offset inside the spawn that starts on *line*.

    The line's start is not enough: an indented or `pub fn` line starts before
    the function does. The spawn's own `Command::new` is the exact place; a call
    to a helper has none, so the last character on the line stands for it.

    Returns
    -------
    int
        An offset the owning function's span contains.
    """
    start, end = _line_span(source.masked, line)
    match = COMMAND_NEW.search(source.masked, start, end)
    if match is not None:
        return match.start()
    return max(start, end - 1)


def _manifest_names(text: str, masked: str) -> set[str]:
    """Return the names bound, by a `let`, to a statement naming a manifest.

    Returns
    -------
    set[str]
        The bound names; a manifest literal counts only where it is code.
    """
    names: set[str] = set()
    for statement in _STATEMENT.finditer(masked):
        if not _names_manifest(text, masked, statement):
            continue
        if _READ_CALL.search(masked, statement.start(), statement.end()):
            continue
        bound = _LET_NAME.match(masked, statement.start())
        if bound is not None:
            names.add(bound.group("name"))
    return names


def _names_manifest(text: str, masked: str, statement: re.Match[str]) -> bool:
    """Report whether a statement holds a manifest literal that is code."""
    return any(
        masked[found.start()] == '"'
        for found in _MANIFEST_LITERAL.finditer(
            text, statement.start(), statement.end()
        )
    )


def _is_scratch_crate(text: str, masked: str) -> bool:
    """Report whether a function writes a manifest into a temporary directory.

    A manifest name in a comment, or in code that only reads one, is not a
    write. The write must be one call that names a manifest literal, or a name
    bound to one, in a function that also holds a temporary directory and points
    its build somewhere (`current_dir` or a manifest path).

    Returns
    -------
    bool
        True when a write of a manifest, a temporary directory and a build
        location are all visible in the function.
    """
    if _TEMPORARY.search(masked) is None or _BUILD_LOCATION.search(text) is None:
        return False
    bound = _manifest_names(text, masked)
    for statement in _STATEMENT.finditer(masked):
        if _WRITE_CALL.search(masked, statement.start(), statement.end()) is None:
            continue
        words = set(_WORD.findall(masked[statement.start() : statement.end()]))
        if _names_manifest(text, masked, statement) or words & bound:
            return True
    return False


def _references(masked: str, names: list[str]) -> list[str]:
    """Return the *names* the function body uses as whole words."""
    return [name for name in names if re.search(rf"\b{re.escape(name)}\b", masked)]


def _spawn_builder(
    source: RustSource, spawn: SpawnFact, names: list[str]
) -> BuilderFact:
    """Return the builder fact for one compiling spawn.

    Returns
    -------
    BuilderFact
        The fact, with the function the spawn sits in and its harness shape.
    """
    offset = _spawn_offset(source, spawn["line"])
    span = function_span(source, offset)
    scratch, references = False, []
    if span is not None:
        body, masked = source.text[span[0] : span[1]], source.masked[span[0] : span[1]]
        scratch = _is_scratch_crate(body, masked)
        references = _references(masked, names)
    return {
        "path": source.relative.as_posix(),
        "line": spawn["line"],
        "function": function_name(source, offset),
        "form": "spawn",
        "readable": spawn["compiles"] != COMPILES_UNKNOWN
        and spawn["state"] != "unresolved",
        "scratch_crate": scratch,
        "harness_references": references,
    }


def _suite_builder(source: RustSource, line: int, form: str) -> BuilderFact:
    """Return the builder fact for a trybuild or macrotest reference.

    Returns
    -------
    BuilderFact
        The fact, declared by file, so it has no function.
    """
    return {
        "path": source.relative.as_posix(),
        "line": line,
        "function": "",
        "form": form,
        "readable": True,
        "scratch_crate": False,
        "harness_references": [],
    }


def _metadata_builders(source: RustSource) -> list[BuilderFact]:
    """Return the `MetadataCommand` uses inside functions of a test source.

    An import, or a use outside any function, owns no query and is not a
    builder.

    Returns
    -------
    list[BuilderFact]
        One fact per use inside a function; none for a source outside a test
        directory.
    """
    if not whole_file_scope(source.relative):
        return []
    found: list[BuilderFact] = []
    for match in _METADATA.finditer(source.masked):
        span = function_span(source, match.start())
        if span is None:
            continue
        found.append({
            "path": source.relative.as_posix(),
            "line": source.masked.count("\n", 0, match.start()) + 1,
            "function": function_name(source, match.start()),
            "form": "metadata",
            "readable": True,
            "scratch_crate": False,
            "harness_references": [],
        })
    return found


def _spawn_builders(
    evidence: RustEvidence, sources: _Sources, names: list[str], problems: list[str]
) -> list[BuilderFact]:
    """Return the builders of every spawn that compiles, or may.

    Returns
    -------
    list[BuilderFact]
        One fact per such spawn.
    """
    found: list[BuilderFact] = []
    for spawn in evidence.spawns:
        source = sources.get(spawn["path"], problems)
        if spawn["compiles"] != COMPILES_NO and source is not None:
            found.append(_spawn_builder(source, spawn, names))
    return found


def _suite_builders(
    evidence: RustEvidence, sources: _Sources, problems: list[str]
) -> list[BuilderFact]:
    """Return the builders of every trybuild and macrotest source reference.

    Returns
    -------
    list[BuilderFact]
        One fact per reference.
    """
    references = (
        (_SUITES[0], [f for f in evidence.trybuild if f["form"] == "source"]),
        (_SUITES[1], evidence.macrotest),
    )
    found: list[BuilderFact] = []
    for form, facts in references:
        for fact in facts:
            source = sources.get(fact["path"], problems)
            if source is not None:
                found.append(_suite_builder(source, fact["line"], form))
    return found


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
        known = merged.setdefault(key, fact)
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
    evidence = collect_rust_evidence(
        checkout, manifests, problems, cold_build=True, compilers=True
    )
    fixtures = fixture_directories(checkout, manifests)
    sources = _Sources(checkout, read_sources(checkout, fixtures, []))
    found = [
        *_spawn_builders(evidence, sources, names, problems),
        *_suite_builders(evidence, sources, problems),
    ]
    for source in sources.loaded():
        found.extend(_metadata_builders(source))
    return _merged(found)
