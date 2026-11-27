"""Find the crate names Cargo discovers from a package's target folders.

An integration test compiles as its own crate named after its file, so
`tests/file_handler_tests.rs` is the crate `file_handler_tests`. Cargo finds
these by looking in `tests`, `benches`, `examples` and `src/bin` (a file
`name.rs`, or a directory with a `main.rs`), unless the package switches that
discovery off with `autotests`, `autobenches`, `autoexamples` or `autobins`.

The reader never follows a symbolic link, so a link out of the checkout cannot
put host filenames into a verdict. Every component of a target folder's path is
checked (`src` as well as `src/bin`), a dangling link counts, and so does a
`main.rs` that is a link. Each link met is recorded as unreadable, so the
policy reports that the crate names it would hold are unknown instead of
reading their absence as a name that does not exist.
"""

from __future__ import annotations

import pathlib
import posixpath
import typing as typ

from .markdown_envelope import is_symlink
from .test_environment_types import table

if typ.TYPE_CHECKING:
    from .test_environment_types import Reader

__all__ = ["discovered_targets", "linked_note"]

OPERATION_SCAN_TARGETS: typ.Final = "scan-rust-targets"

# Each conventional target folder, with the `[package]` switch that turns its
# discovery off.
_TARGET_FOLDERS: typ.Final = (
    ("tests", "autotests"),
    ("benches", "autobenches"),
    ("examples", "autoexamples"),
    ("src/bin", "autobins"),
)


_TARGET_KINDS: typ.Final = ("bin", "test", "bench", "example")


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


def _claimed_paths(manifest: dict[str, object]) -> set[str]:
    """Return the source paths the manifest's declared targets give explicitly.

    Cargo does not infer a second target from a file a declared target already
    names with `path`, so `[[test]] name = "current" path = "tests/old.rs"`
    leaves no `old` target.

    Returns
    -------
    set[str]
        The normalised `path` values, relative to the package directory.
    """
    claimed: set[str] = set()
    for kind in _TARGET_KINDS:
        declared = manifest.get(kind)
        for target in declared if isinstance(declared, list) else []:
            path = table(target).get("path")
            if isinstance(path, str):
                claimed.add(posixpath.normpath(path))
    return claimed


def discovered_targets(
    directory: pathlib.Path, manifest: dict[str, object], reader: Reader
) -> set[str]:
    """Return the crate names Cargo auto-discovers under a package directory.

    A folder whose `[package]` switch is `false` (`autotests = false`) is not
    discovered; only its declared targets count. A file a declared target
    already names by `path` infers no further target.

    Returns
    -------
    set[str]
        The names of the files and directories in each discovered folder.
    """
    package = table(manifest.get("package"))
    claimed = _claimed_paths(manifest)
    found: set[str] = set()
    for sub, switch in _TARGET_FOLDERS:
        folder = (
            None
            if package.get(switch) is False
            else _target_folder(directory, sub, reader)
        )
        if folder is None:
            continue
        inferred = (
            _inferred_target(entry, reader) for entry in sorted(folder.iterdir())
        )
        found |= {
            name
            for name, source in filter(None, inferred)
            if source.relative_to(directory).as_posix() not in claimed
        }
    return found
