"""Follow `netsuke build <target>` from a Makefile recipe into the Netsukefile.

Some repositories keep their lint recipe in a Netsuke manifest: the Makefile's
`lint` runs `netsuke build lint`, and the manifest's `lint` action depends on
the action that runs `cargo clippy`. The rule cannot see that from the
Makefile, so this module reads the manifest the way Netsuke does, as far as the
question needs: the named targets, their `deps`, and each entry's `command`.

It decides nothing. For a `netsuke build` command it returns either "a clippy
run is reachable" or a problem the audit cannot get past (a manifest that is
missing or does not parse, a target that is not defined, a command it cannot
lex). The caller records a problem as an unresolved run, so the policy reports
an indeterminate verdict instead of judging text it could not read.

Template tags (`{{ }}`, `{% %}`) are not evaluated: expressions become single
words and a command that depends on a conditional block is read as written.
Several entries may share a name (Netsuke selects among them with `when:`);
every one of them is read, so a clippy run in any variant counts.
"""

from __future__ import annotations

import dataclasses
import pathlib
import re
import typing as typ

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from .interrogate_invocation import lex_segments
from .make_commands import blank_references, command_start, is_cargo_clippy
from .markdown_envelope import is_file, read_text, within_checkout

MANIFEST_NAME: typ.Final = "Netsukefile"
OPERATION_READ_MANIFEST: typ.Final = "read-netsuke-manifest"
MAX_SCRIPT_DEPTH: typ.Final = 3
_SHELLS: typ.Final = frozenset({"sh", "bash", "zsh", "dash", "ksh"})
_JINJA_EXPRESSION: typ.Final = re.compile(r"\{\{.*?\}\}", re.DOTALL)
_JINJA_BLOCK: typ.Final = re.compile(r"\{%.*?%\}", re.DOTALL)
# Options of `netsuke build` that take a value as the next word.
_VALUE_OPTIONS: typ.Final = frozenset({
    "-f",
    "--file",
    "-C",
    "--directory",
    "-j",
    "--jobs",
    "--locale",
    "--fetch-allow-scheme",
    "--fetch-allow-host",
    "--fetch-block-host",
    "--fetch-default-deny",
})


class Verdict(typ.NamedTuple):
    """What a `netsuke build` command leads to, as far as clippy is concerned."""

    is_run: bool
    problem: str | None


@dataclasses.dataclass(frozen=True, slots=True)
class _Request:
    """The manifest path and the target names a `netsuke build` command names."""

    manifest: str
    targets: list[str]


def _entries(document: object) -> list[dict[str, typ.Any]]:
    """Return the `actions` and `targets` mappings of a manifest, in order."""
    if not isinstance(document, dict):
        return []
    found: list[dict[str, typ.Any]] = []
    for key in ("actions", "targets"):
        section = document.get(key)
        if isinstance(section, list):
            items = typ.cast("list[object]", section)
            found.extend(
                typ.cast("dict[str, typ.Any]", item)
                for item in items
                if isinstance(item, dict)
            )
    return found


def _strings(value: object) -> list[str]:
    """Return *value* as a list of strings: a string, or the strings in a list."""
    match value:
        case str():
            return [value]
        case list():
            return [
                item
                for item in typ.cast("list[object]", value)
                if isinstance(item, str)
            ]
        case _:
            return []


def _names(entry: dict[str, typ.Any]) -> list[str]:
    """Return the names an entry answers to (`name` is a string or a list)."""
    return _strings(entry.get("name"))


def _dependencies(entry: dict[str, typ.Any]) -> list[str]:
    """Return the names an entry depends on (`deps` is a string or a list)."""
    return _strings(entry.get("deps"))


def _plain(command: str) -> str:
    """Return a manifest command with its template tags reduced to words.

    `$$` is Netsuke's escape for a literal `$`, so it becomes `$` first; the
    make-reference reducer then turns `${CARGO:-cargo}` into the word `cargo`.

    Returns
    -------
    str
        The command with template tags and make references reduced to words.
    """
    text = _JINJA_BLOCK.sub(" ", command)
    text = _JINJA_EXPRESSION.sub(" jinja ", text)
    return blank_references(text.replace("$$", "$"))


def _runs_clippy(command: str, depth: int = 0) -> tuple[bool, str | None]:
    """Return whether a manifest command runs `cargo clippy`, or why it cannot say.

    A `sh -c '<script>'` command is read through its script. A command the lexer
    rejects that mentions clippy is a problem, not an absence.

    Returns
    -------
    tuple[bool, str | None]
        Whether a clippy run was found, and a problem if the text was unreadable.
    """
    segments = lex_segments(_plain(command))
    if segments is None:
        return False, (
            "a command it cannot read mentions clippy" if "clippy" in command else None
        )
    for words in segments:
        if is_cargo_clippy(words):
            return True, None
        start = command_start(words)
        if start >= len(words) or depth >= MAX_SCRIPT_DEPTH:
            continue
        name = words[start].strip().lstrip("@-+").rsplit("/", 1)[-1]
        if name in _SHELLS and "-c" in words[start + 1 :]:
            index = words.index("-c", start + 1)
            if index + 1 < len(words):
                found, problem = _runs_clippy(words[index + 1], depth + 1)
                if found or problem:
                    return found, problem
    return False, None


def _request(words: list[str]) -> _Request | None:
    """Return the manifest and targets of a `netsuke build` command, if it is one."""
    start = command_start(words)
    if (
        start >= len(words)
        or words[start].strip().lstrip("@-+").rsplit("/", 1)[-1] != "netsuke"
    ):
        return None
    arguments = iter(words[start + 1 :])
    manifest, directory, targets, seen_build = MANIFEST_NAME, "", [], False
    for word in arguments:
        if word in {"-f", "--file"}:
            manifest = next(arguments, manifest)
        elif word in {"-C", "--directory"}:
            directory = next(arguments, "")
        elif word in _VALUE_OPTIONS:
            next(arguments, None)
        elif word.startswith("-"):
            continue
        elif not seen_build and word == "build":
            seen_build = True
        elif seen_build:
            targets.append(word)
        else:
            return None
    if not seen_build:
        return None
    path = (
        (pathlib.PurePosixPath(directory) / manifest).as_posix()
        if directory
        else manifest
    )
    return _Request(path, targets)


@dataclasses.dataclass(slots=True)
class ManifestReader:
    """Where manifests are read from: one checkout, with each file parsed once."""

    checkout: pathlib.Path
    root: pathlib.Path
    cache: dict[str, tuple[object, str | None]] = dataclasses.field(
        default_factory=dict
    )


def _read(reader: ManifestReader, relative: str) -> tuple[object, str | None]:
    """Parse a manifest, or return the reason it cannot be read."""
    path = reader.checkout / relative
    if ".." in relative.split("/"):
        return None, f"{relative} lies outside the checkout"
    if not is_file(path, OPERATION_READ_MANIFEST):
        return None, f"{relative} does not exist"
    if not within_checkout(reader.root, path, OPERATION_READ_MANIFEST):
        return None, f"{relative} lies outside the checkout"
    try:
        text = read_text(path, OPERATION_READ_MANIFEST)
        return YAML(typ="safe").load(text), None
    except (YAMLError, UnicodeDecodeError) as error:
        return None, f"{relative} cannot be parsed ({type(error).__name__})"


def _load(reader: ManifestReader, relative: str) -> tuple[object, str | None]:
    """Return the parsed manifest from the cache, reading it on first use."""
    if relative not in reader.cache:
        reader.cache[relative] = _read(reader, relative)
    return reader.cache[relative]


def _defaults(document: object) -> list[str]:
    """Return the manifest's `defaults` list."""
    if not isinstance(document, dict):
        return []
    return _strings(typ.cast("dict[str, object]", document).get("defaults"))


def _follow(
    manifest: str, entries: list[dict[str, typ.Any]], targets: list[str]
) -> Verdict:
    """Walk the named targets and their dependencies, reading each command."""
    pending, seen = list(targets), set[str]()
    while pending:
        name = pending.pop(0)
        if name in seen:
            continue
        seen.add(name)
        matching = [entry for entry in entries if name in _names(entry)]
        if not matching:
            return Verdict(
                is_run=False, problem=f"{manifest} defines no target {name!r}"
            )
        for entry in matching:
            command = entry.get("command")
            if isinstance(command, str):
                found, problem = _runs_clippy(command)
                if found or problem:
                    return Verdict(is_run=found, problem=problem)
            pending.extend(_dependencies(entry))
    return Verdict(is_run=False, problem=None)


def netsuke_verdict(reader: ManifestReader, words: list[str]) -> Verdict | None:
    """Return what a simple command leads to, or ``None`` if it is not a build.

    Returns
    -------
    Verdict | None
        Whether a clippy run is reachable, or the problem that stops the audit.
    """
    request = _request(words)
    if request is None:
        return None
    document, error = _load(reader, request.manifest)
    if error is not None:
        return Verdict(is_run=False, problem=error)
    targets = request.targets or _defaults(document)
    if not targets:
        return Verdict(
            is_run=False, problem=f"{request.manifest} names no default target"
        )
    return _follow(request.manifest, _entries(document), targets)
