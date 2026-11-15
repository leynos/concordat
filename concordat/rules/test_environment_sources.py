"""Find the attributes that silence the process-environment lint in Rust files.

Clippy, after macro expansion, finds the calls to `std::env::set_var`,
`remove_var` and `set_current_dir`. This module only finds the ways its verdict
could be switched off (TE-003), so a file is read with tree-sitter and never
searched as text. `test_environment_walk` lists the files of each member,
`test_environment_sites` finds the attributes in a parsed file (including those
written inside a macro body) and `test_environment_attributes` decides what each
one means.

A file that cannot be read, or whose syntax error names the lint, one of its
groups or `warnings`, is reported as unreadable: an unread file is not evidence
of absence. A syntax error elsewhere is ignored, because syntax the parser
predates cannot hide a suppression of this lint.
"""

from __future__ import annotations

import dataclasses
import typing as typ

import tree_sitter
import tree_sitter_rust

from .prover_rust_use import descendants, node_text
from .test_environment_attributes import Suppression, judge
from .test_environment_sites import attribute_sites
from .test_environment_walk import rust_files

if typ.TYPE_CHECKING:
    import pathlib

__all__ = ["SourceScan", "Suppression", "scan_suppressions"]

_LANGUAGE: typ.Final = tree_sitter.Language(tree_sitter_rust.language())

# Words that make a syntax error relevant: the lint, the groups it is in, and
# the group of every warning.
RELEVANT_WORDS: typ.Final = (
    "disallowed_methods",
    "clippy::style",
    "clippy::all",
    "warnings",
)


@dataclasses.dataclass(slots=True)
class SourceScan:
    """The suppressions found, and the files that could not be judged."""

    suppressions: list[Suppression] = dataclasses.field(default_factory=list)
    unreadable: list[str] = dataclasses.field(default_factory=list)


def _is_relevant_error(node: tree_sitter.Node) -> bool:
    """Return whether a syntax error's text names the lint or a group."""
    if node.type != "ERROR" and not node.is_missing:
        return False
    return any(word in node_text(node) for word in RELEVANT_WORDS)


def _error_line(root: tree_sitter.Node) -> int | None:
    """Return the line of a syntax error naming the lint or a group, if any."""
    if not root.has_error:
        return None
    return next(
        (n.start_point.row + 1 for n in descendants(root) if _is_relevant_error(n)),
        None,
    )


def _file_facts(
    source: pathlib.Path,
    checkout: pathlib.Path,
    parser: tree_sitter.Parser,
    silencing: frozenset[str],
) -> tuple[list[Suppression], str | None]:
    """Return one file's suppressions, or the reason it could not be judged."""
    relative = source.relative_to(checkout).as_posix()
    try:
        data = source.read_bytes()
    except OSError as error:
        return [], f"{relative}: {error}"
    root = parser.parse(data).root_node
    line = _error_line(root)
    if line is not None:
        return [], (
            f"{relative}: syntax the parser could not read near line {line} "
            "names the disallowed_methods lint, so whether it is suppressed is "
            "unknown"
        )
    return [
        suppression
        for site in attribute_sites(root)
        for suppression in judge(site, relative, silencing)
    ], None


def scan_suppressions(
    checkout: pathlib.Path,
    member_directories: typ.Sequence[pathlib.Path],
    silencing: typ.Iterable[str],
) -> SourceScan:
    """Return the suppressions in the Rust files of every member crate.

    Parameters
    ----------
    checkout:
        The checkout under audit; reported paths are relative to it.
    member_directories:
        The directory of each workspace member (absolute).
    silencing:
        The lint paths that silence the rule: the lint and its groups.

    Returns
    -------
    SourceScan
        Findings sorted by path and line, and the files that could not be read.
    """
    scan = SourceScan()
    parser = tree_sitter.Parser(_LANGUAGE)
    lints = frozenset(silencing)
    members = frozenset(member_directories)
    sources = sorted({
        source
        for directory in sorted(members)
        for source in rust_files(directory, members, scan.unreadable)
    })
    for source in sources:
        found, problem = _file_facts(source, checkout, parser, lints)
        scan.suppressions += found
        scan.unreadable += [problem] if problem is not None else []
    scan.suppressions.sort(key=lambda s: (s.path, s.line, s.kind))
    return scan
