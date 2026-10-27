"""Resolve which Rust files a checkout's test scope reaches.

Test scope in :mod:`nested_cargo_spawns` is not only a matter of directory
names. A `#[cfg(test)]` item is test code for the span of that item, and a
`mod name;` declaration in test scope pulls the file it names into the test
target as well — whether the declaration carries a `#[cfg(test)]` of its own or
sits inside a test-scope inline module, which a test build compiles just the
same. Resolving a declaration means knowing Rust's own rules: which directory
an inline module's children live in, what `#[path = "..."]` overrides, and
which of `name.rs` and `name/mod.rs` a bare declaration names.

The paths a declaration produces come from repository text, so each is folded
and then refused unless it stays inside the checkout: a `#[path]` value or a
symbolic link must not become the way an audit reads a file outside the
repository it was pointed at.

This module is deliberately separate from :mod:`nested_cargo_spawns`, which
reports the facts. The split keeps both readable: the scope rules are stated
once here rather than woven through the fact collection that consumes them.
"""

from __future__ import annotations

import dataclasses
import pathlib
import re
import typing as typ

from .fs_probe import probe_file
from .nested_cargo_commands import first_literal, split_top_level
from .nested_cargo_source import (
    RustSource,
    TestScope,
    brace_pairs,
    enclosing_block,
    in_fixture,
)
from .rust_source import scan_balanced, skip_trivia

CRATE_ROOT_STEMS: typ.Final = frozenset({"lib", "main", "mod"})

# Cargo builds every `.rs` file directly inside one of these directories as a
# target of its own, so such a file is also a crate root.
CRATE_ROOT_DIRECTORIES: typ.Final = frozenset({"tests", "benches", "examples"})

_ATTRIBUTE: typ.Final = re.compile(r"#\[")
_MODULE: typ.Final = re.compile(r"mod\s+(?P<name>[A-Za-z_]\w*)")
_MODIFIERS: typ.Final = re.compile(r"(?:pub(?:\s*\([^)]*\))?\s+)*")
_ENCLOSING_MODULE: typ.Final = re.compile(r"\bmod\s+([A-Za-z_]\w*)\s*$")
_CFG_PREDICATE: typ.Final = re.compile(r"cfg\s*\((.*)\)\s*$", re.DOTALL)
_PATH_ATTRIBUTE: typ.Final = re.compile(r"\bpath\s*=\s*\"[^\"]*\"")


@dataclasses.dataclass(frozen=True, slots=True)
class Reach:
    """How the compiler was seen reaching one source file.

    A file can be reached in both ways at once — `tests/it.rs` is a target
    Cargo builds on its own, and is also a file another test's `mod it;` may
    name — so each flag records evidence rather than a single verdict.

    Attributes
    ----------
    by_name:
        Declared by a plain `mod name;`, which makes the file a module whose
        own children live in a directory named after it.
    by_path:
        Named by `#[path = "..."]`, which keeps the file's children beside it.
    """

    by_name: bool = False
    by_path: bool = False

    def merged(self, other: Reach) -> Reach:
        """Return the reach that has everything either of these recorded.

        Returns
        -------
        Reach
            The union of both observations.
        """
        return Reach(
            by_name=self.by_name or other.by_name,
            by_path=self.by_path or other.by_path,
        )


@dataclasses.dataclass(frozen=True, slots=True)
class _Declaration:
    """One out-of-line `mod name;` declaration and where Rust looks for it.

    The declaration is in test scope either through its own `#[cfg(test)]`
    attribute or because it sits inside a test-scope inline module; the file it
    names is test code either way.

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


def _predicate_mentions_test(predicate: str) -> bool:
    """Report whether a `cfg` predicate requires the `test` flag."""
    text = predicate.strip()
    if text == "test":
        return True
    head, has_open, _ = text.partition("(")
    if not has_open or not text.endswith(")"):
        return False
    if head.strip() not in {"all", "any"}:
        return False
    inner = text[len(head) + 1 : -1]
    return any(
        _predicate_mentions_test(part)
        for part in split_top_level(inner)
        if part.strip()
    )


def _is_test_attribute(masked: str, match: re.Match[str]) -> bool:
    """Report whether the attribute at *match* is a `cfg` requiring `test`."""
    close = scan_balanced(masked, match.end() - 1)
    if close is None:
        return False
    body = masked[match.end() : close - 1].strip()
    predicate = _CFG_PREDICATE.match(body)
    return predicate is not None and _predicate_mentions_test(predicate.group(1))


def _item_end(masked: str, index: int) -> tuple[int, bool] | None:
    """Return an item's end and whether it opens a body, or None if unbalanced.

    Returns
    -------
    tuple[int, bool] | None
        The index after the item, and whether the item is one with a `{}` body.
    """
    depth = 0
    position = index
    while position < len(masked):
        char = masked[position]
        if char in "([":
            depth += 1
        elif char in ")]":
            depth -= 1
        elif depth == 0 and char == ";":
            return position + 1, False
        elif depth == 0 and char == "{":
            close = scan_balanced(masked, position)
            return (close, True) if close is not None else None
        position += 1
    return None


def _modifier_end(masked: str, index: int) -> int:
    """Return the index after any visibility modifiers at *index*."""
    match = _MODIFIERS.match(masked, skip_trivia(masked, index))
    return match.end() if match is not None else index


def _attribute_run_end(masked: str, start: int) -> int:
    """Return the index after the run of attributes beginning at *start*."""
    index = start
    while True:
        after = skip_trivia(masked, index)
        if not masked.startswith("#[", after):
            return after
        close = scan_balanced(masked, after + 1)
        if close is None:
            return after
        index = close


def _module_chain(
    masked: str, pairs: list[tuple[int, int]], offset: int
) -> tuple[str, ...]:
    """Return the inline modules enclosing *offset*, outermost first.

    Returns
    -------
    tuple[str, ...]
        The module names whose bodies contain the offset.
    """
    chain: list[str] = []
    current = offset
    while (block := enclosing_block(pairs, current)) is not None:
        match = _ENCLOSING_MODULE.search(masked[: block[0]])
        if match is not None:
            chain.append(match.group(1))
        current = block[0]
    return tuple(reversed(chain))


def is_crate_root(relative: pathlib.PurePosixPath) -> bool:
    """Report whether Cargo builds this file as a target of its own.

    A crate root resolves its declarations against its own directory, exactly
    as `mod.rs` does. Cargo treats `lib.rs` and `main.rs` as roots, and also
    every `.rs` file directly inside `tests/`, `benches/`, `examples/` or
    `src/bin/`, because it discovers those as targets without a manifest entry.

    Returns
    -------
    bool
        True when the file is a crate root by Cargo's target discovery.
    """
    if relative.stem in CRATE_ROOT_STEMS:
        return True
    parent = relative.parent
    if parent.name in CRATE_ROOT_DIRECTORIES:
        return True
    return parent.name == "bin" and parent.parent.name == "src"


def _own_directories(
    relative: pathlib.PurePosixPath,
    chain: tuple[str, ...],
    reach: Reach,
) -> tuple[pathlib.PurePosixPath, ...]:
    """Return the directories this file's declarations may be based in.

    A file reached by `#[path = "..."]` keeps its children beside it: the
    compiler resolves `mod helpers;` in `tests/support/probe.rs`, pulled in by
    `#[path]`, against `tests/support/`, not against a `probe/` directory, and
    rejects `tests/support/probe/helpers.rs` with E0583.

    A file reached by a plain `mod name;` declaration is instead a module of
    the file that declares it, so its own declarations resolve against a
    directory named after it. A crate root is a third case: Cargo builds it as
    a target of its own and resolves its declarations against its own
    directory. `tests/it.rs` can be all three at once — a target, a module of
    another test file, and the target of a `#[path]` declaration — and every
    reading is compiled, so every base is offered and the caller keeps
    whichever resolves on disk.

    A file that no scan reached keeps the stem-directory reading, which is the
    only one available without knowing how the compiler got there.

    Returns
    -------
    tuple[pathlib.PurePosixPath, ...]
        The candidate directories, most likely first.
    """
    parent = relative.parent
    is_root = is_crate_root(relative)
    bases: list[pathlib.PurePosixPath] = []
    if reach.by_path or is_root:
        bases.append(parent)
    if reach.by_name or not (is_root or reach.by_path):
        stem = parent / relative.stem
        if stem not in bases:
            bases.append(stem)
    return tuple(base.joinpath(*chain) for base in bases)


def _module_files(
    relative: pathlib.PurePosixPath,
    chain: tuple[str, ...],
    name: str,
    path_attribute: str | None,
    reach: Reach,
) -> list[pathlib.PurePosixPath]:
    """Return the paths an out-of-line module declaration may name.

    `#[path = "..."]` overrides the name entirely. Rust resolves it against
    the declaring file's own directory when the declaration is at the top
    level, and against the module's directory when it sits inside an inline
    module block.

    Returns
    -------
    list[pathlib.PurePosixPath]
        Every file the declaration may name, most likely first.
    """
    if path_attribute is not None:
        owner = (
            relative.parent
            if not chain
            else _own_directories(relative, chain, reach)[0]
        )
        return [owner / path_attribute]
    return [
        candidate
        for base in _own_directories(relative, chain, reach)
        for candidate in (base / f"{name}.rs", base / name / "mod.rs")
    ]


def _folded(candidate: pathlib.PurePosixPath) -> pathlib.PurePosixPath | None:
    """Return *candidate* with `.` and `..` folded out, or `None` if it escapes.

    Rust resolves a `#[path]` value against the declaring file's directory, so
    a step back that stays inside the checkout is ordinary: whitaker declares
    `#[path = "../tests/behaviour.rs"]` from `src/driver/mod.rs`, which names
    `src/tests/behaviour.rs`. Folding first keeps that path readable and makes
    the fact it produces name the file once, in canonical form, rather than
    carrying `src/driver/../tests/...` into a report.

    Folding is lexical, exactly as the module system treats it, so a symbolic
    link is still resolved separately by the caller.

    Returns
    -------
    pathlib.PurePosixPath | None
        The folded path, or ``None`` when it climbs above the checkout root.
    """
    parts: list[str] = []
    for part in candidate.parts:
        if part in {"", "."}:
            continue
        if part == "..":
            if not parts:
                return None
            parts.pop()
            continue
        parts.append(part)
    return pathlib.PurePosixPath(*parts) if parts else pathlib.PurePosixPath()


def _refused_candidate(
    checkout: pathlib.Path, root: pathlib.Path, candidate: pathlib.PurePosixPath
) -> str | None:
    """Return why a declared module path falls outside the checkout, if it does.

    A `#[path = "..."]` value is checkout-controlled text. An absolute value
    replaces the base path entirely, and a symbolic link can point anywhere.
    Reading such a file would carry its spawn facts and its path into the
    envelope, so the candidate is refused before the probe follows it. The
    sibling readers already refuse inputs outside the checkout; a module
    declaration must not be the way around them.

    The caller folds `..` out first, since Rust resolves the value against the
    declaring file and an intermediate step back can legitimately land inside
    the checkout. What reaches here is therefore already normalised.

    Returns
    -------
    str | None
        The reason the candidate is refused, or ``None`` when it may be read.
    """
    if candidate.is_absolute():
        return "names an absolute path, which the checkout cannot supply"
    path = checkout / candidate.as_posix()
    if not probe_file(path).present:
        # Absence is the probe's answer, not a refusal; the caller reports it.
        return None
    try:
        resolved = path.resolve(strict=True)
    except OSError:
        return None
    if resolved != root and not resolved.is_relative_to(root):
        return f"resolves to {resolved}, outside the checkout"
    return None


def _attribute_runs(masked: str) -> dict[int, tuple[int, int]]:
    """Map the token each attribute run precedes to that run's span.

    Every attribute start is visited, including the second and later ones of a
    run; they all report the same run, so the mapping holds one entry per
    attributed item.

    Returns
    -------
    dict[int, tuple[int, int]]
        The offset of the token after the run (past any visibility modifier),
        and the run's `(start, end)`.
    """
    runs: dict[int, tuple[int, int]] = {}
    for match in _ATTRIBUTE.finditer(masked):
        end = _attribute_run_end(masked, match.start())
        # Every attribute of a run reaches the same item and the same run end,
        # so the first start is kept: it is the run's own start, and a later
        # attribute would hide an earlier `cfg` from `_has_cfg`.
        runs.setdefault(_modifier_end(masked, end), (match.start(), end))
    return runs


def _has_cfg(masked: str, run: tuple[int, int]) -> bool:
    """Report whether an attribute run carries a `cfg` attribute.

    Returns
    -------
    bool
        True when a `cfg` gates the item, however its predicate reads.
    """
    for match in _ATTRIBUTE.finditer(masked, *run):
        close = scan_balanced(masked, match.end() - 1)
        if close is None:
            # An attribute that never closes hides the item's own condition, so
            # the declaration is treated as gated rather than assumed test code.
            return True
        if _CFG_PREDICATE.match(masked[match.end() : close - 1].strip()) is not None:
            return True
    return False


def _inherited_declarations(
    source: RustSource, scope: TestScope, pairs: list[tuple[int, int]]
) -> list[_Declaration]:
    """Return the declarations that inherit test scope from an enclosing item.

    `#[cfg(test)] mod checks { mod helpers; }` makes the compiler build
    `src/checks/helpers.rs` into the test target although the declaration
    carries no attribute of its own. Following only the attributed form left
    that file without test scope, so a cargo spawn in it produced no NC-007.

    A declaration whose own attribute run carries a `cfg` is left to the
    attributed pass: a test-requiring one is already recorded there, and any
    other may keep the item out of a test build, which the reader cannot
    decide. `#[path = "..."]` is not a `cfg`, so it is read here.

    Returns
    -------
    list[_Declaration]
        The inherited-scope out-of-line declarations, in source order.
    """
    runs = _attribute_runs(source.masked)
    found: list[_Declaration] = []
    for match in _MODULE.finditer(source.masked):
        if not scope.contains(match.start()):
            continue
        item = _item_end(source.masked, match.end())
        if item is None or item[1]:
            continue
        run = runs.get(match.start())
        if run is not None and _has_cfg(source.masked, run):
            continue
        found.append(
            _Declaration(
                chain=_module_chain(source.masked, pairs, match.start()),
                name=match.group("name"),
                path=_path_attribute(source, run) if run is not None else None,
            )
        )
    return found


def _path_attribute(source: RustSource, run: tuple[int, int]) -> str | None:
    """Return the file `#[path = "..."]` names inside an attribute run, if any.

    The attribute run is located in the masked text, where the value is blanked,
    and read from the original text at the same offsets.

    Returns
    -------
    str | None
        The declared path, or None when the run has no readable `path`.
    """
    start, end = run
    found = _PATH_ATTRIBUTE.search(source.masked, start, end)
    if found is None:
        return None
    return first_literal(source.text[found.start() : found.end()])


def scan_scope(
    source: RustSource, problems: list[str], known: TestScope | None = None
) -> tuple[TestScope, list[_Declaration]]:
    """Return the test spans of one file and the modules it declares out of line.

    Parameters
    ----------
    source:
        The file to scan.
    problems:
        The fact list; an item that never closes appends one sentence.
    known:
        The scope already established for this file. A file a declaration
        pulled into test scope is test code throughout, and its own
        declarations must then be read as test code too — otherwise the
        second hop of `lib.rs -> checks.rs -> checks/helpers.rs` is lost.

    Returns
    -------
    tuple[TestScope, list[_Declaration]]
        The spans already known to be test scope, and each `mod name;`
        declaration in test scope with the file it names.
    """
    scope = TestScope(whole_file=known is not None and known.whole_file)
    declarations: list[_Declaration] = []
    pairs = brace_pairs(source.masked)
    for match in _ATTRIBUTE.finditer(source.masked):
        if not _is_test_attribute(source.masked, match):
            continue
        run = (match.start(), _attribute_run_end(source.masked, match.start()))
        declaration = skip_trivia(source.masked, _modifier_end(source.masked, run[1]))
        item = _item_end(source.masked, declaration)
        if item is None:
            problems.append(
                f"{source.relative.as_posix()}: a #[cfg(test)] item never closes"
            )
            continue
        end, has_body = item
        module = _MODULE.match(source.masked, declaration)
        if module is not None and not has_body:
            declarations.append(
                _Declaration(
                    chain=_module_chain(source.masked, pairs, match.start()),
                    name=module.group("name"),
                    path=_path_attribute(source, run),
                )
            )
            continue
        scope.ranges.append((match.start(), end))
    scope.ranges.sort()
    # The inherited pass runs after the loop so `scope.contains` can answer for
    # the whole file, including the inline module bodies found above.
    declarations.extend(_inherited_declarations(source, scope, pairs))
    return scope, declarations


def _declaration_problem(
    source: RustSource, declaration: _Declaration, reason: str
) -> str:
    """Return one problem sentence about a module declaration.

    The declaration may be in test scope through its own `#[cfg(test)]`
    attribute or through an enclosing test module, so the message names the
    declaration itself rather than an attribute it may not carry.

    Returns
    -------
    str
        The sentence to record in the envelope's problem facts.
    """
    return (
        f"{source.relative.as_posix()}: the mod {declaration.name}; declaration "
        f"{reason}"
    )


@dataclasses.dataclass(frozen=True, slots=True)
class Discovered:
    """The files one scan found, split by what the caller must do with them.

    Attributes
    ----------
    fresh:
        Files not seen before, which the caller must read and scan.
    touched:
        Files whose scope or reach this scan changed. A file already scanned
        may have been read before the evidence existed — as ordinary library
        code, or before its `#[path]` declaration was reached — so its earlier
        scan could not see the declarations it now holds. The caller compares
        each one against the state it was last scanned in and rescans only
        when that state has moved.
    """

    fresh: tuple[pathlib.PurePosixPath, ...]
    touched: tuple[pathlib.PurePosixPath, ...]


def resolve_declarations(
    checkout: pathlib.Path,
    source: RustSource,
    declarations: list[_Declaration],
    scopes: dict[pathlib.PurePosixPath, TestScope],
    reaches: dict[pathlib.PurePosixPath, Reach],
    fixtures: frozenset[str],
    problems: list[str],
) -> Discovered:
    """Record whole-file test scope for each file a declaration names.

    Returns
    -------
    Discovered
        The files to read for the first time, and the ones whose state moved.
    """
    root = checkout.resolve()
    discovered: list[pathlib.PurePosixPath] = []
    touched: list[pathlib.PurePosixPath] = []
    reach = reaches.get(source.relative, Reach())
    for declaration in declarations:
        present: list[pathlib.PurePosixPath] = []
        for declared in _module_files(
            source.relative,
            declaration.chain,
            declaration.name,
            declaration.path,
            reach,
        ):
            named = declared.as_posix()
            if declared.is_absolute():
                problems.append(
                    _declaration_problem(
                        source,
                        declaration,
                        f"names an absolute path, which the checkout cannot "
                        f"supply ({named})",
                    )
                )
                continue
            candidate = _folded(declared)
            if candidate is None:
                problems.append(
                    _declaration_problem(
                        source,
                        declaration,
                        f"climbs out of the checkout with `..` ({named})",
                    )
                )
                continue
            refused = _refused_candidate(checkout, root, candidate)
            if refused is not None:
                problems.append(
                    _declaration_problem(
                        source,
                        declaration,
                        f"{refused} ({candidate.as_posix()})",
                    )
                )
                continue
            if probe_file(checkout / candidate.as_posix()).present:
                present.append(candidate)
        if not present:
            named = (
                declaration.path
                or f"{declaration.name}.rs or {declaration.name}/mod.rs"
            )
            problems.append(
                _declaration_problem(source, declaration, f"names no file ({named})")
            )
            continue
        observed = Reach(
            by_path=declaration.path is not None, by_name=declaration.path is None
        )
        for candidate in present:
            if in_fixture(candidate, fixtures):
                continue
            before = reaches.get(candidate, Reach())
            reaches[candidate] = before.merged(observed)
            known = scopes.get(candidate)
            if known is None:
                scopes[candidate] = TestScope(whole_file=True)
                discovered.append(candidate)
            elif not known.whole_file:
                # Read as ordinary library code, whose scan cannot see the
                # declarations only test code holds.
                known.whole_file = True
                touched.append(candidate)
            elif before != reaches[candidate]:
                # Already test code, but reached in a way it was not before, so
                # its own declarations may now resolve elsewhere.
                touched.append(candidate)
    return Discovered(fresh=tuple(discovered), touched=tuple(touched))
