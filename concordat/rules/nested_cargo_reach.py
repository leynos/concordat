"""Record how the compiler reaches a Rust source file.

The module resolver in :mod:`nested_cargo_modules` follows `mod` declarations
and `#[path]` attributes. What it learns about each file it reaches is small
enough to live apart from the resolution rules, and the preparation reader
needs it too: a file that is only compiled inside the test binaries that
declare it is prepared when those binaries are.
"""

from __future__ import annotations

import dataclasses


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
    roots:
        The Cargo targets (`tests/ui.rs`, `src/lib.rs`) whose module tree
        contains the file, directly or through any chain of declarations. A
        file in a test binary's tree is compiled by that binary, so a rule that
        prepares the binary prepares the file; empty when none was seen.
    """

    by_name: bool = False
    by_path: bool = False
    roots: frozenset[str] = frozenset()

    def __or__(self, other: Reach) -> Reach:
        """Return the reach that has everything either of these recorded.

        Returns
        -------
        Reach
            The union of both observations.
        """
        return Reach(
            by_name=self.by_name or other.by_name,
            by_path=self.by_path or other.by_path,
            roots=self.roots | other.roots,
        )
