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
import typing as typ

from .markdown_envelope import is_symlink

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


def _target_name(entry: pathlib.Path, reader: Reader) -> str | None:
    """Return the crate name Cargo gives a file or directory in a target folder."""
    if _is_linked(entry, reader):
        return None
    if entry.is_file() and entry.suffix == ".rs":
        return entry.stem
    is_binary_directory = entry.is_dir() and not _is_linked(entry / "main.rs", reader)
    return entry.name if is_binary_directory and (entry / "main.rs").is_file() else None


def discovered_targets(
    directory: pathlib.Path, package: dict[str, object], reader: Reader
) -> set[str]:
    """Return the crate names Cargo auto-discovers under a package directory.

    A folder whose `[package]` switch is `false` (`autotests = false`) is not
    discovered; only its declared targets count.

    Returns
    -------
    set[str]
        The names of the files and directories in each discovered folder.
    """
    found: set[str] = set()
    for sub, switch in _TARGET_FOLDERS:
        if package.get(switch) is False:
            continue
        folder = _target_folder(directory, sub, reader)
        if folder is None:
            continue
        names = (_target_name(entry, reader) for entry in sorted(folder.iterdir()))
        found |= {name for name in names if name is not None}
    return found
