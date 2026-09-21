"""Locate a repository's recorded codegen-backend exception in its guide.

The estate standard is per repository: Cranelift is the development-profile
default wherever the repository's own suite passes under it, and where the
suite fails the repository records the failing tests as an exception in its
developers' guide and refuses the backend by contract. The rule accepts either
state and refuses a repository that has neither.

Recognising the exception means reading documentation, which is prose. What is
structure, and therefore checkable, is the heading: a section whose heading
names the backend, and the channel spellings that section's body contains. The
channel is the useful half, because an exception that still names the pinned
toolchain is current and one that names an older toolchain is due a re-test.

Headings are extracted by walking the document rather than by matching a
pattern against the whole text, so a heading-shaped line inside a fenced code
block — which every one of these sections contains, since the failing tests are
quoted — is not mistaken for a section of its own.
"""

from __future__ import annotations

import re
import typing as typ

if typ.TYPE_CHECKING:
    import pathlib

_HEADING: typ.Final = re.compile(r"^(?P<hashes>#{1,6})\s+(?P<text>.+?)\s*#*\s*$")
_FENCE: typ.Final = re.compile(r"^\s*(?P<fence>`{3,}|~{3,})")
# The channel spellings rustup accepts, plus a bare version. Anything looser
# would match a library version quoted in the evidence and make a stale
# exception read as current.
_CHANNEL: typ.Final = re.compile(
    r"\b(?:nightly-\d{4}-\d{2}-\d{2}|beta-\d{4}-\d{2}-\d{2}"
    r"|nightly|beta|stable|\d+\.\d+(?:\.\d+)?)\b"
)


class ExceptionSection(typ.TypedDict):
    """One documented exception section and the evidence it carries."""

    path: str
    heading: str
    line: int
    level: int
    channels_named: list[str]
    body_lines: int


class DocumentScan(typ.TypedDict):
    """The outcome of scanning one declared document."""

    path: str
    present: bool
    read_error: str | None
    sections: list[ExceptionSection]


class _Heading(typ.NamedTuple):
    """One ATX heading found outside every fenced code block."""

    level: int
    text: str
    line: int


def find_exception_sections(
    checkout: pathlib.Path,
    documents: typ.Sequence[str],
    keyword: str,
) -> list[DocumentScan]:
    """Scan each declared document for sections whose heading names *keyword*.

    Returns
    -------
    list[DocumentScan]
        One entry per declared document, in the order declared.
    """
    return [_scan_document(checkout, relative, keyword) for relative in documents]


def _scan_document(
    checkout: pathlib.Path,
    relative: str,
    keyword: str,
) -> DocumentScan:
    """Return the scan of one declared document."""
    path = checkout / relative
    if not path.is_file():
        return {
            "path": relative,
            "present": False,
            "read_error": None,
            "sections": [],
        }
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        return {
            "path": relative,
            "present": True,
            "read_error": str(error),
            "sections": [],
        }
    lines = text.splitlines()
    return {
        "path": relative,
        "present": True,
        "read_error": None,
        "sections": _sections_naming(relative, lines, keyword),
    }


def _sections_naming(
    relative: str,
    lines: list[str],
    keyword: str,
) -> list[ExceptionSection]:
    """Return every section in *lines* whose heading contains *keyword*."""
    headings = list(extract_headings(lines))
    folded = keyword.casefold()
    sections: list[ExceptionSection] = []
    for index, heading in enumerate(headings):
        if folded not in heading.text.casefold():
            continue
        body = _section_body(lines, headings, index)
        sections.append({
            "path": relative,
            "heading": heading.text,
            "line": heading.line,
            "level": heading.level,
            "channels_named": sorted(set(_CHANNEL.findall("\n".join(body)))),
            "body_lines": len([line for line in body if line.strip()]),
        })
    return sections


def _section_body(
    lines: list[str],
    headings: list[_Heading],
    index: int,
) -> list[str]:
    """Return the body of the heading at *index*, up to the next peer heading.

    A subsection belongs to its parent, so the body runs to the next heading of
    the same level or shallower, not merely to the next heading.

    Returns
    -------
    list[str]
        The lines beneath the heading, excluding the heading itself.
    """
    heading = headings[index]
    start = heading.line
    end = len(lines)
    for following in headings[index + 1 :]:
        if following.level <= heading.level:
            end = following.line - 1
            break
    return lines[start:end]


def extract_headings(lines: typ.Sequence[str]) -> typ.Iterator[_Heading]:
    """Yield every ATX heading in *lines* that lies outside a fenced block.

    Yields
    ------
    _Heading
        Each heading's level, text, and one-based line number.
    """
    fence: str | None = None
    for number, line in enumerate(lines, start=1):
        fence, is_fence = _advance_fence(fence, line)
        if is_fence or fence is not None:
            continue
        match = _HEADING.match(line)
        if match is not None:
            yield _Heading(
                level=len(match.group("hashes")),
                text=match.group("text").strip(),
                line=number,
            )


def _advance_fence(fence: str | None, line: str) -> tuple[str | None, bool]:
    """Return the fence state after *line*, and whether *line* was a fence.

    Returns
    -------
    tuple[str | None, bool]
        The open fence marker after this line, and whether this line opened or
        closed one.
    """
    match = _FENCE.match(line)
    if match is None:
        return fence, False
    marker = match.group("fence")
    if fence is None:
        return marker, True
    # A closing fence must use the same character and be at least as long as
    # the fence it closes; a shorter or different run is content.
    if marker[0] == fence[0] and len(marker) >= len(fence):
        return None, True
    return fence, False
