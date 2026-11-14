"""Read the `rust-prover-tools` revisions a checkout names (PT-001).

A revision is named in `tools/rust-prover-tools/REF`, in a literal
`rust-prover-tools@<ref>` in a Makefile variable or recipe, or in a workflow
command. The reference is kept whole, as written, so `main` or `<sha>-patched`
reaches the policy and fails PT-001 rather than being read as the revision it
starts with. A reference that is itself a substitution (`$(shell cat REF)`) is
not a literal: the REF file is the pin there.
"""

from __future__ import annotations

import typing as typ

from .fs_probe import regular_file_exists
from .markdown_envelope import within_checkout

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    import pathlib

    from .makefile_facts import MakeutilReport
    from .prover_tooling_types import Pin

OPERATION: typ.Final = "read-prover-tooling-facts"
REF_FILE: typ.Final = "tools/rust-prover-tools/REF"
PIN_MARKER: typ.Final = "rust-prover-tools@"
REVISION_LENGTH: typ.Final = 40
REFERENCE_END: typ.Final = frozenset(" \t\"'),;")
HEX_DIGITS: typ.Final = frozenset("0123456789abcdef")
SOURCE_VARIABLE_PREFIX: typ.Final = "PROVER_TOOLS"


def is_revision(text: str) -> bool:
    """Return whether *text* is a 40-digit lowercase hexadecimal revision."""
    return len(text) == REVISION_LENGTH and set(text) <= HEX_DIGITS


def _until_end(tail: str) -> str:
    """Return *tail* up to the first character that ends a reference."""
    return next(
        (tail[:i] for i, char in enumerate(tail) if char in REFERENCE_END), tail
    )


def literal_reference(text: str) -> str | None:
    """Return the whole reference following `rust-prover-tools@` in *text*.

    Returns
    -------
    str | None
        The reference as written, or None when there is none or it is a
        substitution rather than a literal.
    """
    if PIN_MARKER not in text:
        return None
    reference = _until_end(text.split(PIN_MARKER, maxsplit=1)[1])
    if not reference:
        return None
    return None if reference.startswith("$") else reference


def _pin(value: str, source: str) -> Pin:
    """Return one named revision with its source."""
    return {"value": value, "source": source}


def _variable_pin(name: str, raw: str) -> str | None:
    """Return the revision a Make variable names, if it names one."""
    reference = literal_reference(raw)
    if reference is not None:
        return reference
    is_source = name.startswith(SOURCE_VARIABLE_PREFIX)
    return raw.strip() if is_source and is_revision(raw.strip()) else None


def makefile_pins(report: MakeutilReport) -> list[Pin]:
    """Return the revisions the Makefile's variables name.

    Returns
    -------
    list[Pin]
        One pin per variable that names a literal revision.
    """
    found: list[Pin] = []
    for variable in typ.cast("list[dict[str, object]]", report["variables"]):
        value = _variable_pin(
            str(variable.get("name", "")), str(variable.get("raw_value", ""))
        )
        if value is not None:
            found.append(_pin(value, "Makefile"))
    return found


def command_pins(argvs: cabc.Iterable[list[str]], source: str) -> list[Pin]:
    """Return the revisions named in the words of *argvs*.

    Returns
    -------
    list[Pin]
        One pin per word that carries a literal `rust-prover-tools@<ref>`.
    """
    references = (literal_reference(word) for argv in argvs for word in argv)
    return [_pin(reference, source) for reference in references if reference]


def _ref_line_value(line: str) -> str | None:
    """Return the reference a REF file line names, if it names one."""
    key, separator, value = line.partition(":")
    if separator and key.strip() == "ref":
        return value.strip() or None
    return line.strip() if is_revision(line.strip()) else None


def ref_file_pins(checkout: pathlib.Path, root: pathlib.Path) -> list[Pin]:
    """Return the revisions `tools/rust-prover-tools/REF` names.

    A `ref:` line names its value as written; a bare line counts only when it
    is a full revision. A REF file that resolves outside the checkout raises
    `OperationalRuleError`.

    Returns
    -------
    list[Pin]
        The revisions named, each with its source.
    """
    path = checkout / REF_FILE
    if not within_checkout(root, path, OPERATION):
        return []
    if not regular_file_exists(path, operation=OPERATION):
        return []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    values = (_ref_line_value(line) for line in lines)
    return [_pin(value, REF_FILE) for value in values if value]
