"""Rust facts for the `policy-input/documentation-presence` envelope.

DG-003 holds every library crate a checkout owns to `missing_docs` at `deny`
(or `forbid`). Whether a crate does is spread over three places, and cargo and
rustc combine them in a fixed order that the policy should not re-derive from
text:

* the crate root's inner attributes (`#![deny(missing_docs)]`), which beat any
  command-line or manifest level;
* the crate's own `[lints.rust]` table;
* the workspace's `[workspace.lints.rust]` table, inherited only by a crate
  that opts in with `[lints] workspace = true`.

This module reads those three and records them as facts per library crate. It
decides nothing: the policy turns the facts into a verdict. A manifest or crate
root it cannot read is carried with an error, so the policy reports an
indeterminate verdict rather than judging text it could not parse.

Item-level suppressions (`#[allow(missing_docs)]` on a module) are out of
scope: the rule checks the crate-level contract, not every item.
"""

from __future__ import annotations

import dataclasses
import os
import pathlib
import re
import tomllib
import typing as typ

from .makefile_expansion import expand, shown
from .markdown_envelope import (
    PRUNED_DIRECTORIES,
    _is_symlink,
    _raise_walk_error,
    _read_text,
    is_file,
    within_checkout,
)

if typ.TYPE_CHECKING:
    import collections.abc as cabc

CARGO_MANIFEST: typ.Final = "Cargo.toml"
LINT_NAME: typ.Final = "missing_docs"
OPERATION_READ_CARGO: typ.Final = "read-cargo-manifest"
OPERATION_READ_CRATE_ROOT: typ.Final = "read-crate-root"
OPERATION_SCAN_CARGO: typ.Final = "scan-cargo-manifests"

# Trees whose crates are test data, samples or third-party code, not crates the
# repository owns and publishes. A fixture crate that is deliberately
# undocumented must not fail the rule that guards the real ones.
RUST_PRUNED_DIRECTORIES: typ.Final = PRUNED_DIRECTORIES | frozenset({
    "target",
    "vendor",
    "third_party",
    "patches",
    "fixtures",
    "fixtures_macros",
    "templates",
    "examples",
})

# `cargo clippy`, `$(CARGO) clippy` and `cargo +nightly clippy` all name clippy
# as a word of its own; `clippy.toml` and `cargo-clippy-driver` do not.
_CLIPPY_WORD: typ.Final = re.compile(r"(?<![\w./-])clippy(?![\w.-])")
_UNRESOLVED_CLIPPY: typ.Final = re.compile(
    r"\$[({][^)}\s]*clippy[^)}\s]*[)}]", re.IGNORECASE
)
PRINTING_COMMANDS: typ.Final = frozenset({"echo", "printf"})

LINT_KINDS: typ.Final = frozenset({"deny", "forbid", "warn", "allow", "expect"})


class RootAttribute(typ.TypedDict):
    """One crate-level inner attribute that names `missing_docs`."""

    kind: str
    has_reason: bool
    condition: str | None


class RustCrate(typ.TypedDict):
    """The `missing_docs` facts of one library crate."""

    manifest: str
    name: str | None
    root: str | None
    root_error: str | None
    manifest_level: str | None
    inherits_workspace: bool
    workspace_level: str | None
    attributes: list[RootAttribute]


class ClippyRun(typ.TypedDict):
    """One recipe line, in a target `lint` reaches, that runs clippy."""

    target: str
    line: int
    command: str
    resolved: bool


class RustFacts(typ.TypedDict):
    """The Rust section of the documentation-presence envelope."""

    crates: list[RustCrate]
    errors: list[str]


@dataclasses.dataclass(frozen=True, slots=True)
class _Manifest:
    """A parsed Cargo.toml and where it sits, relative to the checkout."""

    path: str
    table: dict[str, typ.Any]

    @property
    def directory(self) -> str:
        """The manifest's directory as a POSIX path, ``""`` at the root."""
        parent = pathlib.PurePosixPath(self.path).parent.as_posix()
        return "" if parent == "." else parent


def _manifest_paths(checkout: pathlib.Path) -> list[str]:
    """Return the checkout's owned Cargo.toml files, as sorted POSIX paths.

    The walk never follows symbolic links and skips fixture, vendored and
    build-output trees. A directory that cannot be listed raises.

    Returns
    -------
    list[str]
        Paths relative to the checkout, sorted.
    """
    found: list[str] = []
    for current, directories, files in os.walk(checkout, onerror=_raise_walk_error):
        directories[:] = sorted(
            name for name in directories if name not in RUST_PRUNED_DIRECTORIES
        )
        base = pathlib.Path(current)
        found.extend(
            (base / name).relative_to(checkout).as_posix()
            for name in files
            if name == CARGO_MANIFEST
            and not _is_symlink(base / name, OPERATION_SCAN_CARGO)
        )
    return sorted(found)


def _level_of(value: object) -> str | None:
    """Return the level of a `[lints]` entry written as a string or a table."""
    match value:
        case str():
            return value
        case {"level": str() as level}:
            return level
        case _:
            return None


def _missing_docs_level(table: object) -> str | None:
    """Return the `missing_docs` level in a `lints` or `workspace.lints` table."""
    if not isinstance(table, dict):
        return None
    rust = table.get("rust")
    return _level_of(rust.get(LINT_NAME)) if isinstance(rust, dict) else None


def _is_library(manifest: _Manifest, *, root_exists: bool) -> bool:
    """Return whether the manifest describes a package with a library target."""
    table = manifest.table
    if not isinstance(table.get("package"), dict):
        return False
    return "lib" in table or root_exists


def _lib_root(manifest: _Manifest) -> str:
    """Return the library root's path: `[lib] path`, else `src/lib.rs`."""
    lib = manifest.table.get("lib")
    custom = lib.get("path") if isinstance(lib, dict) else None
    relative = custom if isinstance(custom, str) else "src/lib.rs"
    return (pathlib.PurePosixPath(manifest.directory) / relative).as_posix()


def _skip_trivia(text: str, index: int) -> int:
    """Return the index of the next token, past whitespace and comments."""
    while index < len(text):
        if text[index].isspace():
            index += 1
        elif text.startswith("//", index):
            end = text.find("\n", index)
            index = len(text) if end == -1 else end + 1
        elif text.startswith("/*", index):
            end = text.find("*/", index + 2)
            index = len(text) if end == -1 else end + 2
        else:
            break
    return index


def _scan_balanced(text: str, start: int) -> int | None:
    """Return the index just past the bracket that closes the one at *start*.

    Strings are skipped, so a `reason` containing a bracket does not end the
    attribute early.

    Returns
    -------
    int | None
        The index after the closing bracket, or ``None`` if it never closes.
    """
    depth, index = 0, start
    while index < len(text):
        char = text[index]
        if char == '"':
            index += 1
            while index < len(text) and text[index] != '"':
                index += 2 if text[index] == "\\" else 1
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    return None


def _split_top_level(text: str) -> list[str]:
    """Split on commas that sit outside brackets and strings."""
    parts: list[str] = []
    depth, start, index = 0, 0, 0
    while index < len(text):
        char = text[index]
        if char == '"':
            index += 1
            while index < len(text) and text[index] != '"':
                index += 2 if text[index] == "\\" else 1
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif char == "," and depth == 0:
            parts.append(text[start:index].strip())
            start = index + 1
        index += 1
    parts.append(text[start:].strip())
    return [part for part in parts if part]


def _lint_attributes(body: str, condition: str | None) -> typ.Iterator[RootAttribute]:
    """Yield the `missing_docs` attributes inside one attribute's text.

    `cfg_attr(condition, inner...)` is unwrapped, carrying its condition, so
    `#![cfg_attr(not(test), deny(missing_docs))]` is seen for what it is.

    Yields
    ------
    RootAttribute
        One entry per lint attribute that names `missing_docs`.
    """
    head, _, rest = body.partition("(")
    name = head.strip()
    arguments = rest.rsplit(")", 1)[0]
    if name == "cfg_attr":
        parts = _split_top_level(arguments)
        if len(parts) >= 2:
            for inner in parts[1:]:
                yield from _lint_attributes(inner, parts[0])
        return
    if name not in LINT_KINDS:
        return
    items = _split_top_level(arguments)
    lints = [item for item in items if not item.startswith("reason")]
    if LINT_NAME in lints:
        yield {
            "kind": name,
            "has_reason": any(item.startswith("reason") for item in items),
            "condition": condition,
        }


def root_attributes(source: str) -> list[RootAttribute]:
    """Return the `missing_docs` inner attributes at the top of a crate root.

    Inner attributes must precede every item, so reading stops at the first
    token that is not one. Comments and inner doc comments are skipped.

    Returns
    -------
    list[RootAttribute]
        The attributes in source order.
    """
    found: list[RootAttribute] = []
    index = _skip_trivia(source, 0)
    while source.startswith("#![", index):
        end = _scan_balanced(source, index + 2)
        if end is None:
            break
        found.extend(_lint_attributes(source[index + 3 : end - 1], None))
        index = _skip_trivia(source, end)
    return found


def _read_manifest(
    checkout: pathlib.Path, root: pathlib.Path, relative: str, errors: list[str]
) -> _Manifest | None:
    """Parse one manifest, recording a failure in *errors* and returning None."""
    path = checkout / relative
    if not within_checkout(root, path, OPERATION_READ_CARGO):
        errors.append(f"{relative}: resolves outside the checkout")
        return None
    try:
        return _Manifest(
            relative, tomllib.loads(_read_text(path, OPERATION_READ_CARGO))
        )
    except UnicodeDecodeError as error:
        errors.append(f"{relative}: not UTF-8 text: {error}")
    except tomllib.TOMLDecodeError as error:
        errors.append(f"{relative}: invalid TOML: {error}")
    return None


def _workspace_level(manifest: _Manifest, workspaces: list[_Manifest]) -> str | None:
    """Return the `missing_docs` level of the nearest workspace above a crate."""
    for candidate in sorted(workspaces, key=lambda m: -len(m.directory)):
        base = candidate.directory
        contains = not base or manifest.directory == base
        contains = contains or manifest.directory.startswith(base + "/")
        if contains:
            table = candidate.table.get("workspace")
            return _missing_docs_level(table.get("lints")) if table else None
    return None


def _crate_facts(
    checkout: pathlib.Path,
    root: pathlib.Path,
    manifest: _Manifest,
    workspaces: list[_Manifest],
) -> RustCrate | None:
    """Return the facts of one manifest, or ``None`` if it has no library."""
    root_relative = _lib_root(manifest)
    root_path = checkout / root_relative
    has_root = within_checkout(root, root_path, OPERATION_READ_CRATE_ROOT) and is_file(
        root_path, OPERATION_READ_CRATE_ROOT
    )
    if not _is_library(manifest, root_exists=has_root):
        return None
    lints = manifest.table.get("lints")
    inherits = isinstance(lints, dict) and lints.get("workspace") is True
    package = manifest.table["package"]
    attributes: list[RootAttribute] = []
    root_error: str | None = None
    if has_root:
        try:
            attributes = root_attributes(
                _read_text(root_path, OPERATION_READ_CRATE_ROOT)
            )
        except UnicodeDecodeError as error:
            root_error = f"not UTF-8 text: {error}"
    else:
        root_error = "the library root file does not exist"
    return {
        "manifest": manifest.path,
        "name": package.get("name") if isinstance(package.get("name"), str) else None,
        "root": root_relative,
        "root_error": root_error,
        "manifest_level": _missing_docs_level(lints),
        "inherits_workspace": inherits,
        "workspace_level": _workspace_level(manifest, workspaces) if inherits else None,
        "attributes": attributes,
    }


def build_rust_facts(checkout: pathlib.Path, root: pathlib.Path) -> RustFacts:
    """Collect the `missing_docs` facts of every library crate in a checkout.

    Parameters
    ----------
    checkout:
        Path to the checkout under audit.
    root:
        The checkout's resolved path, for the containment guard.

    Returns
    -------
    RustFacts
        One entry per library crate, and the manifests that would not parse.
    """
    errors: list[str] = []
    manifests = [
        parsed
        for relative in _manifest_paths(checkout)
        if (parsed := _read_manifest(checkout, root, relative, errors)) is not None
    ]
    workspaces = [m for m in manifests if isinstance(m.table.get("workspace"), dict)]
    crates = [
        facts
        for manifest in manifests
        if (facts := _crate_facts(checkout, root, manifest, workspaces)) is not None
    ]
    return {"crates": crates, "errors": errors}


def clippy_runs(
    index: cabc.Mapping[str, cabc.Sequence[typ.Any]],
    reachable: list[str],
    values: cabc.Mapping[str, str],
) -> list[ClippyRun]:
    """Return every recipe line in a reachable target that runs clippy.

    *index* maps a target to its rules, each with a `recipes` list of
    `(line, text)` pairs. A line that only prints text is not a run. A line that
    names clippy as a word is a run even when other parts of it (a `RUSTFLAGS`
    assignment built with `$(if ...)` or `$(shell ...)`, as the estate's
    Makefiles have) do not expand: those parts do not change which tool runs. A
    line that reaches clippy only through a variable named for it (`$(CLIPPY)`)
    is carried as unresolved, so the policy cannot read it as proof either way.

    Returns
    -------
    list[ClippyRun]
        The lines in target and line order.
    """
    runs: list[ClippyRun] = []
    for target in reachable:
        for rule in index[target]:
            for line, text in rule.recipes:
                text_shown = shown(expand(text, values).text)
                words = text_shown.split()
                if words and words[0].lstrip("@-+") in PRINTING_COMMANDS:
                    continue
                is_run = _CLIPPY_WORD.search(text_shown) is not None
                if is_run or _UNRESOLVED_CLIPPY.search(text_shown):
                    runs.append({
                        "target": target,
                        "line": line,
                        "command": " ".join(text_shown.split()),
                        "resolved": is_run,
                    })
    return runs
