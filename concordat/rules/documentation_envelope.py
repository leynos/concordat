"""Build the `policy-input/documentation-presence` envelope.

The documentation-presence rule package holds a checkout to one standard: every
Python file it owns is covered by an `interrogate` run that `make lint` reaches,
at a threshold of 100, with no relaxing ignore flag, and with a guard that
fails on any `MISSED` row. The policy cannot decide that from raw Makefile
text, because the run is usually spelled through variables
(`$(INTERROGATE) $(INTERROGATE_TARGETS)`) and sits in a target the `lint`
target reaches only through prerequisites. This module therefore does the
mechanical part: it expands the Make variables the Makefile defines, follows
`lint` through its prerequisites and `$(MAKE)` calls, and records every
`interrogate` invocation it finds as a structured fact. The policy decides what
the facts mean.

Each fact is recorded as it was found. A recipe the expansion could not fully
resolve (a Make function, an unset variable, a quoting the tokenizer rejects)
is carried with `resolved: false`, so the policy reports an indeterminate
verdict rather than judging text it could not read. The Python files come from
a walk that never follows symbolic links and prunes the directories that never
hold owned source.

The file readers are `markdown_envelope`'s, so every envelope shares one
containment guard: no policy input is read from outside the checkout.
"""

from __future__ import annotations

import dataclasses
import os
import pathlib
import posixpath
import re
import shlex
import tomllib
import types
import typing as typ

from .makefile_facts import MakefileInspector, inspect_makefile
from .markdown_envelope import (
    OPERATION_PROBE_PATH,
    OPERATION_READ_MAKEFILE,
    PRUNED_DIRECTORIES,
    Repository,
    _is_symlink,
    _raise_walk_error,
    _read_text,
    is_file,
    resolved_root,
    within_checkout,
)

if typ.TYPE_CHECKING:
    import collections.abc as cabc

    from .makefile_facts import MakeutilReport, MakeVariable

ENVELOPE_SCHEMA_VERSION: typ.Final = 1
ENVELOPE_KIND: typ.Final = "policy-input/documentation-presence"
LINT_TARGET: typ.Final = "lint"
PYPROJECT_FILENAME: typ.Final = "pyproject.toml"
OPERATION_READ_CONFIG: typ.Final = "read-interrogate-config"
OPERATION_SCAN_PYTHON: typ.Final = "scan-python-files"

# Directories that hold third-party or generated Python, on top of the
# directories every envelope prunes. Their files are not the repository's.
PYTHON_PRUNED_DIRECTORIES: typ.Final = PRUNED_DIRECTORIES | frozenset({
    "vendor",
    "site-packages",
    "venv",
    ".tox",
})

# How many times a value may name another variable before the expansion gives
# up. A cycle never settles, so it ends as unresolved rather than looping.
EXPANSION_PASSES: typ.Final = 8

# `interrogate`'s options, from `interrogate --help` at 1.7.0. Short options
# that take a value, long options that take a value, and the relaxing flags.
VALUE_SHORT: typ.Final = frozenset("ferwog")
VALUE_LONG: typ.Final = frozenset({
    "--fail-under",
    "--exclude",
    "--ignore-regex",
    "--whitelist-regex",
    "--style",
    "--output",
    "--generate-badge",
    "--badge-format",
    "--badge-style",
    "--config",
    "--ext",
})
RELAXING_SHORT: typ.Final = {
    "i": "--ignore-init-method",
    "I": "--ignore-init-module",
    "m": "--ignore-magic",
    "M": "--ignore-module",
    "n": "--ignore-nested-functions",
    "C": "--ignore-nested-classes",
    "p": "--ignore-private",
    "P": "--ignore-property-decorators",
    "S": "--ignore-setters",
    "s": "--ignore-semiprivate",
}
RELAXING_LONG: typ.Final = frozenset(RELAXING_SHORT.values())
# Ignoring `@typing.overload` stubs (`-O`) is not a relaxation: Ruff forbids a
# docstring on them. It is absent from the tables above, so it is never reported.
SEGMENT_BREAKS: typ.Final = frozenset({";", "&&", "||", "|", "&", "(", ")", ";;"})
# A command that only prints text mentioning interrogate is not an invocation.
PRINTING_COMMANDS: typ.Final = frozenset({"echo", "printf", "@echo", "@printf"})
# Flags before the tool name that take it as their value: `uv run --with
# interrogate interrogate` names the package twice and runs it once.
INSTALL_FLAGS: typ.Final = frozenset({"--with", "--from", "install"})

_SHELL_DOLLAR: typ.Final = "\x00"
# Stands for a Make reference the expansion left; it lexes as a word of its own.
_UNRESOLVED_MARK: typ.Final = "\x01"
# Variables GNU Make defines itself. The Makefile under audit is the root one,
# so its own list is that file alone.
BUILTIN_VARIABLES: typ.Final = types.MappingProxyType({
    "MAKEFILE_LIST": "Makefile",
    "MAKE": "make",
    "CURDIR": ".",
})
_FUNCTION_CALL: typ.Final = re.compile(
    r"\$\((dir|notdir|lastword|firstword|abspath|realpath|strip)\s+([^()$]*)\)"
)
_VARIABLE_REFERENCE: typ.Final = re.compile(r"\$[({]([A-Za-z_][A-Za-z0-9_]*)[)}]")
_UNRESOLVED_REFERENCE: typ.Final = re.compile(r"\$[({]")
_MAKE_CALL: typ.Final = re.compile(r"(?:\$\(MAKE\)|\bmake)\s+(?:-\S+\s+)*([\w.-]+)")
_UNRESOLVED_TOOL: typ.Final = re.compile(
    r"\$[({][^)}\s]*interrogate[^)}\s]*[)}]", re.IGNORECASE
)
_GUARD: typ.Final = re.compile(r"grep[^\n]*MISSED")


class InterrogateRun(typ.TypedDict):
    """One `interrogate` invocation inside a recipe `lint` reaches."""

    target: str
    line: int
    command: str
    resolved: bool
    paths: list[str]
    fail_under: str | None
    verbose: int
    config: str | None
    excludes: list[str]
    ignores: list[str]
    guarded: bool


class InterrogateConfig(typ.TypedDict):
    """A configuration file an invocation reads, decoded or carrying its error."""

    path: str
    table: dict[str, object] | None
    error: str | None


class DocumentationApplicability(typ.TypedDict):
    """Evidence that the documentation-presence rule package may apply."""

    root_makefile: bool
    python_files: bool
    pyproject: bool


class DocumentationEnvelope(typ.TypedDict):
    """The `policy-input/documentation-presence` document."""

    schema_version: int
    kind: str
    repository: Repository
    applicability: DocumentationApplicability
    makefile_path: str | None
    lint_target: bool
    reachable_targets: list[str]
    reachability_resolved: bool
    interrogate_elsewhere: list[str]
    runs: list[InterrogateRun]
    configs: list[InterrogateConfig]
    python_files: list[str]


@dataclasses.dataclass(frozen=True, slots=True)
class _Expansion:
    """A Make text after variable substitution, and whether it fully resolved."""

    text: str
    is_resolved: bool


def _shown(text: str) -> str:
    """Return *text* with the placeholders turned back into what they stood for."""
    return text.replace(_SHELL_DOLLAR, "$").replace(_UNRESOLVED_MARK, "$")


def _joined(text: str) -> str:
    """Return *text* with backslash-newline continuations folded to spaces."""
    return re.sub(r"\\\n[ \t]*", " ", text)


def _assignments(variables: cabc.Sequence[MakeVariable]) -> dict[str, str]:
    r"""Return each variable that has exactly one plain assignment, and its value.

    A variable assigned twice, conditionally or in a `define` block has no one
    value the expansion can substitute, so it is left out and a reference to it
    stays unresolved. Make turns `\#` in a value into `#`, so that is undone
    here and in no recipe.

    Returns
    -------
    dict[str, str]
        Variable name to its raw value.
    """
    counts: dict[str, int] = {}
    for variable in variables:
        name = str(variable.get("name", ""))
        counts[name] = counts.get(name, 0) + 1
    values: dict[str, str] = {}
    for variable in variables:
        name = str(variable.get("name", ""))
        conditions = variable.get("conditions", [])
        is_plain = (
            counts[name] == 1
            and not variable.get("define_block", False)
            and not (isinstance(conditions, list) and conditions)
        )
        if is_plain:
            values[name] = _joined(str(variable.get("raw_value", ""))).replace(
                "\\#", "#"
            )
    return values


def _call_function(match: re.Match[str]) -> str:
    """Evaluate the Make text functions that build file names.

    `abspath` and `realpath` normalise and stay relative: the checkout is the
    Makefile's own directory, which is all those calls name in practice.

    Returns
    -------
    str
        The function's result, as Make would produce it for these arguments.
    """
    name, arguments = match.group(1), match.group(2).split()
    match name:
        case "dir":
            results = [
                word[: word.rfind("/") + 1] if "/" in word else "./"
                for word in arguments
            ]
        case "notdir":
            results = [word.rsplit("/", 1)[-1] for word in arguments]
        case "lastword":
            results = arguments[-1:]
        case "firstword":
            results = arguments[:1]
        case "strip":
            results = arguments
        case _:
            results = [posixpath.normpath(word) for word in arguments]
    return " ".join(results)


def _expand(text: str, values: cabc.Mapping[str, str]) -> _Expansion:
    """Substitute `$(NAME)` references until the text settles.

    `$$` is a shell dollar, not a reference. It stays as a placeholder in the
    result so the lexer can tell shell substitution from a Make reference left
    behind; :func:`_shown` turns it back into `$`. Any reference left after the
    passes (a Make function, an unset or ambiguous variable, a cycle) marks the
    expansion unresolved.

    Returns
    -------
    _Expansion
        The substituted text and whether every reference was resolved.
    """
    current = text.replace("$$", _SHELL_DOLLAR)

    known = {**BUILTIN_VARIABLES, **values}

    def substitute(match: re.Match[str]) -> str:
        value = known.get(match.group(1))
        # A `$$` in a value is a shell dollar once the reference is expanded.
        return match.group(0) if value is None else value.replace("$$", _SHELL_DOLLAR)

    for _ in range(EXPANSION_PASSES):
        updated = _FUNCTION_CALL.sub(
            _call_function, _VARIABLE_REFERENCE.sub(substitute, current)
        )
        if updated == current:
            break
        current = updated
    return _Expansion(current, _UNRESOLVED_REFERENCE.search(current) is None)


def _rules_by_target(
    report: MakeutilReport,
) -> dict[str, list[tuple[list[str], list[tuple[int, str]]]]]:
    """Index rules by target: prerequisites and `(line, recipe)` pairs per rule.

    Returns
    -------
    dict[str, list[tuple[list[str], list[tuple[int, str]]]]]
        Every rule that names the target, in file order.
    """
    index: dict[str, list[tuple[list[str], list[tuple[int, str]]]]] = {}
    for rule in report["rules"]:
        recipes = [
            (recipe["location"]["start_line"], _joined(recipe["text"]))
            for recipe in rule["recipes"]
        ]
        for target in rule["targets"]:
            index.setdefault(target, []).append((rule["prerequisites"], recipes))
    return index


def _reachable(
    index: dict[str, list[tuple[list[str], list[tuple[int, str]]]]],
    values: cabc.Mapping[str, str],
) -> tuple[list[str], bool]:
    """Return the targets `lint` reaches, and whether the walk resolved fully.

    The walk follows prerequisites and `$(MAKE) name` or `make name` calls in
    recipes. A prerequisite or call that names a variable the expansion cannot
    settle is skipped and clears the resolved flag, because the target it would
    have named is unknown.

    Returns
    -------
    tuple[list[str], bool]
        The reachable targets in discovery order, and whether none were skipped.
    """
    seen: list[str] = []
    is_resolved = True
    pending = [LINT_TARGET]
    while pending:
        target = pending.pop(0)
        if target in seen or target not in index:
            continue
        seen.append(target)
        for prerequisites, recipes in index[target]:
            names = list(prerequisites)
            for _line, text in recipes:
                names.extend(_MAKE_CALL.findall(_expand(text, values).text))
            for name in names:
                expanded = _expand(name, values)
                is_resolved = is_resolved and expanded.is_resolved
                pending.extend(expanded.text.split())
    return seen, is_resolved


def _segments(command: str) -> list[list[str]] | None:
    """Split a shell command into simple commands, or ``None`` if it won't lex.

    Command substitution is rewritten to a parenthesised group first, so an
    invocation inside `"$(...)"` is a command of its own and not a fragment of
    one quoted word.

    Returns
    -------
    list[list[str]] | None
        The words of each simple command, or ``None`` when the lexer refuses.
    """
    flattened = re.sub(rf'"?{_SHELL_DOLLAR}\(', " ( ", command)
    flattened = re.sub(r'\)"', " ) ", flattened)
    flattened = re.sub(r"\$([({])", rf"{_UNRESOLVED_MARK} \1", flattened)
    lexer = shlex.shlex(flattened, posix=True, punctuation_chars=";&|()")
    lexer.whitespace_split = True
    try:
        words = list(lexer)
    except ValueError:
        return None
    segments: list[list[str]] = [[]]
    for raw in words:
        word = raw.replace(_SHELL_DOLLAR, "$")
        if word in SEGMENT_BREAKS:
            segments.append([])
        else:
            segments[-1].append(word)
    return [segment for segment in segments if segment]


def _is_tool_word(words: list[str], index: int) -> bool:
    """Return whether ``words[index]`` runs interrogate rather than names it."""
    word = words[index]
    if posixpath.basename(word) != "interrogate":
        return False
    return index == 0 or words[index - 1] not in INSTALL_FLAGS


def _scan_options(arguments: list[str]) -> dict[str, typ.Any]:
    """Read the options and paths that follow the `interrogate` word.

    Returns
    -------
    dict[str, typing.Any]
        `paths`, `fail_under`, `verbose`, `config`, `excludes` and `ignores`.
    """
    scanned: dict[str, typ.Any] = {
        "paths": [],
        "fail_under": None,
        "verbose": 0,
        "config": None,
        "excludes": [],
        "ignores": [],
    }
    queue = list(arguments)
    while queue:
        word = queue.pop(0)
        if word.startswith("--"):
            flag, has_value, value = word.partition("=")
            if flag in VALUE_LONG and not has_value and queue:
                value = queue.pop(0)
            _record_long(scanned, flag, value)
        elif word.startswith("-") and len(word) > 1:
            _record_short(scanned, word, queue)
        elif not re.match(r"^\d*[<>]", word):
            scanned["paths"].append(posixpath.normpath(word))
    return scanned


def _record_long(scanned: dict[str, typ.Any], flag: str, value: str) -> None:
    """Record one long option in *scanned*."""
    if flag == "--fail-under":
        scanned["fail_under"] = value
    elif flag == "--verbose":
        scanned["verbose"] += 1
    elif flag == "--config":
        scanned["config"] = posixpath.normpath(value)
    elif flag == "--exclude":
        scanned["excludes"].append(value)
    elif flag == "--ignore-regex":
        scanned["ignores"].append(f"--ignore-regex {value}")
    elif flag in RELAXING_LONG:
        scanned["ignores"].append(flag)


def _record_short(scanned: dict[str, typ.Any], word: str, queue: list[str]) -> None:
    """Record a short option cluster such as `-vv`, `-nC` or `-f100`."""
    letters = word[1:]
    for position, letter in enumerate(letters):
        if letter == "v":
            scanned["verbose"] += 1
        elif letter in VALUE_SHORT:
            attached = letters[position + 1 :]
            value = attached or (queue.pop(0) if queue else "")
            _record_long(scanned, _LONG_FOR_SHORT.get(letter, f"-{letter}"), value)
            return
        elif letter in RELAXING_SHORT:
            scanned["ignores"].append(RELAXING_SHORT[letter])


_LONG_FOR_SHORT: typ.Final = {
    "f": "--fail-under",
    "e": "--exclude",
    "r": "--ignore-regex",
}


def _runs_in(
    target: str,
    line: int,
    raw: str,
    expansion: _Expansion,
    *,
    is_guarded: bool,
) -> list[InterrogateRun]:
    """Return every `interrogate` invocation in one expanded recipe line.

    Returns
    -------
    list[InterrogateRun]
        The invocations, or one unresolved fact when the line cannot be read
        but names interrogate.
    """
    if not re.search(r"(?<![\w-])interrogate(?![\w-])", expansion.text):
        # `$(INTERROGATE)` that expanded to something unreadable leaves no
        # `interrogate` word to find, yet it is an invocation; losing it would
        # read as no gate at all.
        if not expansion.is_resolved and _UNRESOLVED_TOOL.search(raw):
            return [_unresolved_run(target, line, raw, is_guarded=is_guarded)]
        return []
    segments = _segments(expansion.text)
    if segments is None:
        return [_unresolved_run(target, line, expansion.text, is_guarded=is_guarded)]
    found: list[InterrogateRun] = []
    for words in segments:
        if words[0].lstrip("@-+") in PRINTING_COMMANDS | {"make"}:
            continue
        for index in range(len(words)):
            if not _is_tool_word(words, index):
                continue
            options = _scan_options(words[index + 1 :])
            found.append({
                "target": target,
                "line": line,
                "command": _shown(" ".join(words[index:])),
                "resolved": _UNRESOLVED_MARK not in words[index:],
                "paths": options["paths"] or ["."],
                "fail_under": options["fail_under"],
                "verbose": options["verbose"],
                "config": options["config"],
                "excludes": options["excludes"],
                "ignores": options["ignores"],
                "guarded": is_guarded,
            })
            break
    return found


def _unresolved_run(
    target: str, line: int, text: str, *, is_guarded: bool
) -> InterrogateRun:
    """Return a placeholder fact for a recipe the tokenizer could not read."""
    return {
        "target": target,
        "line": line,
        "command": _shown(text),
        "resolved": False,
        "paths": ["."],
        "fail_under": None,
        "verbose": 0,
        "config": None,
        "excludes": [],
        "ignores": [],
        "guarded": is_guarded,
    }


def _interrogate_runs(
    index: dict[str, list[tuple[list[str], list[tuple[int, str]]]]],
    reachable: list[str],
    values: cabc.Mapping[str, str],
) -> list[InterrogateRun]:
    """Return every invocation in the recipes of the reachable targets.

    A run counts as guarded when any recipe line of its target pipes output
    through `grep` for `MISSED`: interrogate rounds its percentage to one
    decimal, so the threshold alone passes a lone gap in a large repository.

    Returns
    -------
    list[InterrogateRun]
        The invocations in target and line order.
    """
    runs: list[InterrogateRun] = []
    for target in reachable:
        for _prerequisites, recipes in index[target]:
            expanded = [(line, text, _expand(text, values)) for line, text in recipes]
            is_guarded = any(_GUARD.search(item.text) for _l, _t, item in expanded)
            for line, text, item in expanded:
                runs.extend(_runs_in(target, line, text, item, is_guarded=is_guarded))
    return runs


def _elsewhere(
    index: dict[str, list[tuple[list[str], list[tuple[int, str]]]]],
    reachable: list[str],
) -> list[str]:
    """Return unreachable targets whose recipes mention interrogate.

    The policy names them: a gate that exists but sits outside `lint` is a
    different defect from no gate at all.

    Returns
    -------
    list[str]
        The target names, sorted.
    """
    return sorted(
        target
        for target, rules in index.items()
        if target not in reachable
        and any(
            re.search(r"(?<![\w-])interrogate(?![\w-])", text)
            for _prerequisites, recipes in rules
            for _line, text in recipes
        )
    )


def _load_config(
    checkout: pathlib.Path, root: pathlib.Path, relative: str
) -> InterrogateConfig | None:
    """Return the `[tool.interrogate]` table of one TOML file, if it exists.

    A file that is not TOML (an `.ini` file) is carried with an error: the
    policy cannot read its threshold, so it reports indeterminate.

    Returns
    -------
    InterrogateConfig | None
        The table (empty when the file has none), or ``None`` for no file.
    """
    path = checkout / relative
    if not within_checkout(root, path, OPERATION_READ_CONFIG) or not is_file(
        path, OPERATION_READ_CONFIG
    ):
        return None
    fact: InterrogateConfig = {"path": relative, "table": None, "error": None}
    if not relative.endswith(".toml"):
        fact["error"] = "not a TOML file; the policy reads only TOML configuration"
        return fact
    try:
        parsed = tomllib.loads(_read_text(path, OPERATION_READ_CONFIG))
    except UnicodeDecodeError as error:
        fact["error"] = f"not UTF-8 text: {error}"
        return fact
    except tomllib.TOMLDecodeError as error:
        fact["error"] = f"invalid TOML: {error}"
        return fact
    tool = parsed.get("tool")
    table = tool.get("interrogate") if isinstance(tool, dict) else None
    fact["table"] = dict(table) if isinstance(table, dict) else {}
    return fact


def _python_files(checkout: pathlib.Path) -> list[str]:
    """Return the checkout's owned Python files, as sorted POSIX paths.

    The walk never follows symbolic links and a link to a file is skipped, as
    in the Markdown scan. A directory that cannot be listed raises rather than
    reading as one with no Python in it.

    Returns
    -------
    list[str]
        Paths relative to the checkout, sorted.
    """
    found: list[str] = []
    for current, directories, files in os.walk(checkout, onerror=_raise_walk_error):
        directories[:] = sorted(
            name for name in directories if name not in PYTHON_PRUNED_DIRECTORIES
        )
        base = pathlib.Path(current)
        found.extend(
            (base / name).relative_to(checkout).as_posix()
            for name in files
            if name.endswith(".py")
            and not _is_symlink(base / name, OPERATION_SCAN_PYTHON)
        )
    return sorted(found)


def build_documentation_envelope(
    checkout: pathlib.Path, *, inspect: MakefileInspector = inspect_makefile
) -> DocumentationEnvelope:
    """Assemble the documentation-presence policy input for one local checkout.

    Parameters
    ----------
    checkout:
        Path to the checkout under audit.
    inspect:
        Reads the root Makefile's facts. The default is the pure query; the
        command boundary injects an observing one.

    An `OperationalRuleError` propagates from the fact readers if the root
    `Makefile` cannot be parsed by `makeutil`, a fact file exists but cannot
    be opened, a directory cannot be listed, or a fact file resolves to a
    location outside the checkout.

    Returns
    -------
    DocumentationEnvelope
        The policy input document assembled from the checkout.
    """
    root = resolved_root(checkout)
    makefile = checkout / "Makefile"
    has_makefile = within_checkout(root, makefile, OPERATION_READ_MAKEFILE) and is_file(
        makefile, OPERATION_PROBE_PATH
    )
    index: dict[str, list[tuple[list[str], list[tuple[int, str]]]]] = {}
    values: dict[str, str] = {}
    makefile_path: str | None = None
    if has_makefile:
        report = inspect(makefile).report
        makefile_path = str(report.get("source", {}).get("path", "Makefile"))
        index = _rules_by_target(report)
        values = _assignments(report["variables"])
    reachable, is_resolved = _reachable(index, values)
    runs = _interrogate_runs(index, reachable, values)
    names = [PYPROJECT_FILENAME]
    names.extend(
        sorted(
            {
                run["config"]
                for run in runs
                if run["config"] and run["resolved"] and "$" not in run["config"]
            }
            - {*names}
        )
    )
    configs = [
        config
        for name in names
        if (config := _load_config(checkout, root, name)) is not None
    ]
    python_files = _python_files(checkout)
    return {
        "schema_version": ENVELOPE_SCHEMA_VERSION,
        "kind": ENVELOPE_KIND,
        "repository": {"path": str(checkout), "name": None},
        "applicability": {
            "root_makefile": has_makefile,
            "python_files": bool(python_files),
            "pyproject": any(c["path"] == PYPROJECT_FILENAME for c in configs),
        },
        "makefile_path": makefile_path,
        "lint_target": LINT_TARGET in index,
        "reachable_targets": reachable,
        "reachability_resolved": is_resolved,
        "interrogate_elsewhere": _elsewhere(index, reachable),
        "runs": runs,
        "configs": configs,
        "python_files": python_files,
    }
