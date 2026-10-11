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

Module-level and item-level suppressions (`#[allow(missing_docs)]` on a module
or item) are recorded too, from the library's source tree, because each hides
items from a crate whose root denies the lint. The policy requires a reason.
"""

from __future__ import annotations

import dataclasses
import os
import pathlib
import tomllib
import typing as typ

from .markdown_envelope import (
    PRUNED_DIRECTORIES,
    _is_symlink,
    _raise_walk_error,
    _read_text,
    is_file,
    within_checkout,
)
from .rust_source import (
    LINT_NAME,
    RootAttribute,
    leading_attributes,
    suppressions_in,
)

CARGO_MANIFEST: typ.Final = "Cargo.toml"
OPERATION_READ_CARGO: typ.Final = "read-cargo-manifest"
OPERATION_READ_CRATE_ROOT: typ.Final = "read-crate-root"
OPERATION_SCAN_CARGO: typ.Final = "scan-cargo-manifests"
OPERATION_SCAN_RUST: typ.Final = "scan-rust-sources"

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


class Suppression(typ.TypedDict):
    """A `missing_docs` allow or expect on a module or item, not the crate root."""

    file: str
    line: int
    kind: str
    has_reason: bool


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
    suppressions: list[Suppression]
    source_errors: list[str]


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


def _scan_sources(
    checkout: pathlib.Path,
    root: pathlib.Path,
    root_relative: str,
    skip_before: int,
) -> tuple[list[Suppression], list[str]]:
    """Scan the library's source tree for module and item level suppressions.

    The tree is the directory holding the library root (`src/`). Tests, benches
    and examples are separate crates and are not part of the library.

    Returns
    -------
    tuple[list[Suppression], list[str]]
        The suppressions in file and line order, and the files that could not
        be decoded.
    """
    base = checkout / pathlib.PurePosixPath(root_relative).parent
    found: list[Suppression] = []
    unreadable: list[str] = []
    if not within_checkout(root, base, OPERATION_SCAN_RUST) or not base.is_dir():
        return found, unreadable
    for current, directories, files in os.walk(base, onerror=_raise_walk_error):
        directories[:] = sorted(
            name for name in directories if name not in RUST_PRUNED_DIRECTORIES
        )
        for name in sorted(files):
            path = pathlib.Path(current) / name
            if not name.endswith(".rs") or _is_symlink(path, OPERATION_SCAN_RUST):
                continue
            relative = path.relative_to(checkout).as_posix()
            try:
                text = _read_text(path, OPERATION_SCAN_RUST)
            except UnicodeDecodeError:
                unreadable.append(relative)
                continue
            skip = skip_before if relative == root_relative else 0
            found.extend(
                {"file": relative, "line": line, "kind": kind, "has_reason": reason}
                for line, kind, reason in suppressions_in(text, skip_before=skip)
            )
    return found, unreadable


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
    skip_before = 0
    if has_root:
        try:
            attributes, skip_before = leading_attributes(
                _read_text(root_path, OPERATION_READ_CRATE_ROOT)
            )
        except UnicodeDecodeError as error:
            root_error = f"not UTF-8 text: {error}"
    else:
        root_error = "the library root file does not exist"
    suppressions, source_errors = _scan_sources(
        checkout, root, root_relative, skip_before
    )
    return {
        "manifest": manifest.path,
        "name": package.get("name") if isinstance(package.get("name"), str) else None,
        "root": root_relative,
        "root_error": root_error,
        "manifest_level": _missing_docs_level(lints),
        "inherits_workspace": inherits,
        "workspace_level": _workspace_level(manifest, workspaces) if inherits else None,
        "attributes": attributes,
        "suppressions": suppressions,
        "source_errors": source_errors,
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
