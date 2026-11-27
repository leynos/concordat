"""Find the crate names Cargo gives a package's targets.

An integration test compiles as its own crate named after its file, so
`tests/file_handler_tests.rs` is the crate `file_handler_tests`. Cargo finds
these by looking in `tests`, `benches`, `examples` and `src/bin` (a file
`name.rs`, or a directory with a `main.rs`), and the package itself is a crate
only through its default targets: `src/lib.rs` is the library and `src/main.rs`
the binary, each named after the package. A package with neither has no crate of
its own name.

Cargo's rules for what is discovered are followed:

* `autotests`, `autobenches`, `autoexamples` and `autobins` switch discovery of a
  kind off; in edition 2015 discovery of a kind is also off when the manifest
  declares a target of that kind and the switch is unset;
* a declared target's `path` stops Cargo inferring a second target of the same
  kind from that file, whatever the declared name.

The reader never follows a symbolic link, so a link out of the checkout cannot
put host filenames into a verdict. Every component of a target folder's path is
checked (`src` as well as `src/bin`), a dangling link counts, and so does a
`main.rs` that is a link. Each link met is recorded as unreadable, so the policy
reports that the crate names it would hold are unknown instead of reading their
absence as a name that does not exist.
"""

from __future__ import annotations

import pathlib
import posixpath
import typing as typ

from .markdown_envelope import is_symlink
from .test_environment_types import table

if typ.TYPE_CHECKING:
    from .test_environment_types import Reader

__all__ = ["default_targets", "discovered_targets", "linked_note"]

OPERATION_SCAN_TARGETS: typ.Final = "scan-rust-targets"


class _Folder(typ.NamedTuple):
    """A conventional target folder: where, its auto-discovery switch, its kind."""

    path: str
    switch: str
    kind: str


_TARGET_FOLDERS: typ.Final = (
    _Folder("tests", "autotests", "test"),
    _Folder("benches", "autobenches", "bench"),
    _Folder("examples", "autoexamples", "example"),
    _Folder("src/bin", "autobins", "bin"),
)


def linked_note(relative: str) -> str:
    """Return why a symbolic link is not read."""
    return (
        f"{relative}: is a symbolic link, which the audit does not follow, so the "
        "crate names or configuration it would hold are unknown"
    )


def _is_linked(path: pathlib.Path, reader: Reader) -> bool:
    """Return whether *path* is a symbolic link, recording it when it is."""
    if not is_symlink(path, OPERATION_SCAN_TARGETS):
        return False
    reader.unreadable.append(linked_note(path.relative_to(reader.checkout).as_posix()))
    return True


def _target_folder(
    directory: pathlib.Path, sub: str, reader: Reader
) -> pathlib.Path | None:
    """Return a target folder if no component of its path is a link, else None."""
    current = directory
    for part in pathlib.PurePosixPath(sub).parts:
        current /= part
        if _is_linked(current, reader):
            return None
    return current if current.is_dir() else None


def _inferred_target(
    entry: pathlib.Path, reader: Reader
) -> tuple[str, pathlib.Path] | None:
    """Return the crate name and source file Cargo infers from a folder entry.

    A file `name.rs` is the target `name`; a directory holding a `main.rs` is the
    target named after the directory (`tests/group/main.rs` is `group`).

    Returns
    -------
    tuple[str, pathlib.Path] | None
        The crate name and its source file, or None for an entry that is not a
        target (or is a link, which is recorded).
    """
    if _is_linked(entry, reader):
        return None
    if entry.is_file() and entry.suffix == ".rs":
        return entry.stem, entry
    if not entry.is_dir():
        return None
    main = entry / "main.rs"
    if _is_linked(main, reader):
        return None
    return (entry.name, main) if main.is_file() else None


def _declared(manifest: dict[str, object], kind: str) -> list[object]:
    """Return the manifest's declared targets of one kind (`[[test]]` and so on)."""
    declared = manifest.get(kind)
    return (
        list(typ.cast("list[object]", declared)) if isinstance(declared, list) else []
    )


def _claimed_paths(manifest: dict[str, object], kind: str) -> set[str]:
    """Return the source paths declared targets of one kind give explicitly.

    Cargo does not infer a second target of a kind from a file a declared target
    of that kind already names with `path`, so
    `[[test]] name = "current" path = "tests/old.rs"` leaves no inferred `old`
    test. A path of another kind claims nothing: an explicit binary at
    `tests/scenario.rs` leaves the inferred test `scenario` alone.

    Returns
    -------
    set[str]
        The normalised `path` values, relative to the package directory.
    """
    paths = (table(target).get("path") for target in _declared(manifest, kind))
    return {posixpath.normpath(path) for path in paths if isinstance(path, str)}


def _is_discovered(manifest: dict[str, object], folder: _Folder) -> bool:
    """Return whether Cargo auto-discovers targets of a folder's kind.

    An explicit switch decides. Unset, discovery is on, except in edition 2015
    (the default when no edition is given), where a manifest that declares a
    target of the kind turns discovery of that kind off.

    Returns
    -------
    bool
        Whether the folder's targets are inferred.
    """
    package = table(manifest.get("package"))
    switch = package.get(folder.switch)
    if isinstance(switch, bool):
        return switch
    edition = package.get("edition")
    is_2015 = edition is None or edition == "2015"
    return not (is_2015 and _declared(manifest, folder.kind))


def default_targets(
    directory: pathlib.Path, manifest: dict[str, object], reader: Reader
) -> set[str]:
    """Return the package name when a default target gives it a crate.

    The library is `src/lib.rs`, or the file a `[lib]` table's `path` names; it
    is named after the package unless `[lib] name` says otherwise (a declared
    name is read elsewhere). The binary is `src/main.rs`, named after the
    package, unless a declared `[[bin]]` claims that path (and so names it) or
    binary discovery is off. A package with neither has no crate named after
    itself.

    Returns
    -------
    set[str]
        The package name, or nothing.
    """
    name = table(manifest.get("package")).get("name")
    source = _target_folder(directory, "src", reader)
    if not isinstance(name, str):
        return set()
    is_named = _library_gives_package_name(manifest, source, reader)
    return (
        {name}
        if is_named or _binary_gives_package_name(manifest, source, reader)
        else set()
    )


def _library_gives_package_name(
    manifest: dict[str, object], source: pathlib.Path | None, reader: Reader
) -> bool:
    """Return whether the library exists and is not renamed by `[lib] name`."""
    library = manifest.get("lib")
    has_library = isinstance(library, dict) or (
        source is not None and _has_file(source / "lib.rs", reader)
    )
    return has_library and not isinstance(table(library).get("name"), str)


def _binary_gives_package_name(
    manifest: dict[str, object], source: pathlib.Path | None, reader: Reader
) -> bool:
    """Return whether `src/main.rs` is discovered and no `[[bin]]` claims it."""
    return (
        source is not None
        and _is_discovered(manifest, _TARGET_FOLDERS[3])
        and "src/main.rs" not in _claimed_paths(manifest, "bin")
        and _has_file(source / "main.rs", reader)
    )


def _has_file(path: pathlib.Path, reader: Reader) -> bool:
    """Return whether *path* is a regular file that is not a link."""
    return not _is_linked(path, reader) and path.is_file()


def discovered_targets(
    directory: pathlib.Path, manifest: dict[str, object], reader: Reader
) -> set[str]:
    """Return the crate names Cargo auto-discovers under a package directory.

    A kind whose discovery is off (see `_is_discovered`) is not discovered; only
    its declared targets count. A file a declared target of the same kind already
    names by `path` infers no further target.

    Returns
    -------
    set[str]
        The names of the files and directories in each discovered folder.
    """
    found: set[str] = set()
    for folder in _TARGET_FOLDERS:
        if not _is_discovered(manifest, folder):
            continue
        root = _target_folder(directory, folder.path, reader)
        if root is None:
            continue
        claimed = _claimed_paths(manifest, folder.kind)
        inferred = (_inferred_target(entry, reader) for entry in sorted(root.iterdir()))
        found |= {
            name
            for name, source in filter(None, inferred)
            if source.relative_to(directory).as_posix() not in claimed
        }
    return found
