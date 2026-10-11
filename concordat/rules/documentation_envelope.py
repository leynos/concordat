"""Build the `policy-input/documentation-presence` envelope.

The documentation-presence rule package holds a checkout to one standard: every
Python file it owns is covered by an `interrogate` run that `make lint` reaches,
at a threshold of 100, with no relaxing ignore flag, and with a guard that
fails on any `MISSED` row. The policy cannot decide that from raw Makefile
text, because the run is usually spelled through variables
(`$(INTERROGATE) $(INTERROGATE_TARGETS)`) and sits in a target the `lint`
target reaches only through prerequisites. This module therefore does the
mechanical part: it expands the Make variables the Makefile defines
(`makefile_expansion`), follows `lint` through its prerequisites and `make`
commands, and records every `interrogate` invocation it finds as a structured
fact (`interrogate_invocation`). The policy decides what the facts mean.

Each fact is recorded as it was found. A recipe the expansion could not fully
resolve (a Make function, an unset variable, a quoting the lexer rejects) is
carried with `resolved: false`, so the policy reports an indeterminate verdict
rather than judging text it could not read. The Python files come from a walk
that never follows symbolic links and prunes the directories that never hold
owned source.

The file readers are `markdown_envelope`'s, so every envelope shares one
containment guard: no policy input is read from outside the checkout.
"""

from __future__ import annotations

import collections
import dataclasses
import os
import pathlib
import re
import tomllib
import typing as typ

from .interrogate_invocation import (
    InterrogateRun,
    RecipeLine,
    has_missed_guard,
    lex_segments,
    runs_in,
)
from .makefile_expansion import UNRESOLVED_MARK, expand, single_assignments
from .makefile_facts import MakefileInspector, inspect_makefile
from .markdown_envelope import (
    OPERATION_PROBE_PATH,
    OPERATION_READ_MAKEFILE,
    PRUNED_DIRECTORIES,
    Repository,
    is_file,
    is_symlink,
    raise_walk_error,
    read_text,
    resolved_root,
    within_checkout,
)

if typ.TYPE_CHECKING:
    import collections.abc as cabc

    from .makefile_facts import MakeutilReport

__all__ = [
    "ENVELOPE_KIND",
    "ENVELOPE_SCHEMA_VERSION",
    "DocumentationEnvelope",
    "InterrogateConfig",
    "InterrogateRun",
    "build_documentation_envelope",
]

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

_NAMES_TOOL: typ.Final = re.compile(r"(?<![\w-])interrogate(?![\w-])")


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


class Rule(typ.NamedTuple):
    """One Make rule: its prerequisites and its `(line, text)` recipe lines."""

    prerequisites: list[str]
    recipes: list[tuple[int, str]]


type RuleIndex = dict[str, list[Rule]]


@dataclasses.dataclass(frozen=True, slots=True)
class MakefileAnalysis:
    """What the root Makefile says about interrogate."""

    path: str | None = None
    has_lint_target: bool = False
    reachable: list[str] = dataclasses.field(default_factory=list)
    is_resolved: bool = True
    elsewhere: list[str] = dataclasses.field(default_factory=list)
    runs: list[InterrogateRun] = dataclasses.field(default_factory=list)


@dataclasses.dataclass(frozen=True, slots=True)
class Successors:
    """The targets a rule leads to, and whether every one could be named."""

    names: list[str]
    is_resolved: bool


def _rules_by_target(report: MakeutilReport) -> RuleIndex:
    """Index rules by target, in file order.

    Returns
    -------
    RuleIndex
        Every rule that names the target.
    """
    index: RuleIndex = {}
    for rule in report["rules"]:
        recipes = [
            (recipe["location"]["start_line"], recipe["text"])
            for recipe in rule["recipes"]
        ]
        for target in rule["targets"]:
            index.setdefault(target, []).append(Rule(rule["prerequisites"], recipes))
    return index


def _goals(arguments: list[str]) -> list[str]:
    """Return the goals of a `make` command: its words that are not options."""
    return [word for word in arguments if not word.startswith("-") and "=" not in word]


def _changes_directory(arguments: list[str]) -> bool:
    """Return whether a `make` command's options move it to another directory."""
    return any(word.startswith(("-C", "--directory")) for word in arguments)


def _make_goals(text: str) -> Successors:
    """Return the targets that `make` commands in an expanded recipe line name.

    Only a command in command position counts, so `echo "run make interrogate"`
    leads nowhere. A line the lexer rejects, or a goal left unresolved, clears
    the resolved flag, because the target it would have named is unknown.

    Returns
    -------
    Successors
        The goals named, and whether the line was fully read.
    """
    segments = lex_segments(text)
    if segments is None:
        return Successors([], is_resolved=not re.search(r"\bmake\b", text))
    commands = [words for words in segments if words[0].lstrip("@-+") == "make"]
    goals = [goal for words in commands for goal in _goals(words[1:])]
    named = [goal for goal in goals if UNRESOLVED_MARK not in goal]
    # `make -C dir target` runs another Makefile, whose `interrogate` is not
    # this one's, so the walk cannot follow it and says so.
    delegates = any(_changes_directory(words[1:]) for words in commands)
    return Successors(named, is_resolved=not delegates and len(named) == len(goals))


def _rule_successors(rule: Rule, values: cabc.Mapping[str, str]) -> Successors:
    """Return the targets one rule leads to, by prerequisite or `make` command.

    Returns
    -------
    Successors
        The target names, and whether every one of them was resolved.
    """
    names: list[str] = []
    is_resolved = True
    expansions = [expand(name, values) for name in rule.prerequisites]
    for expansion in expansions:
        names.extend(expansion.text.split())
        is_resolved = is_resolved and expansion.is_resolved
    for _line, text in rule.recipes:
        found = _make_goals(expand(text, values).text)
        names.extend(found.names)
        is_resolved = is_resolved and found.is_resolved
    return Successors(names, is_resolved)


def _reachable(index: RuleIndex, values: cabc.Mapping[str, str]) -> Successors:
    """Return the targets `lint` reaches, and whether the walk resolved fully.

    The walk follows prerequisites and `make name` commands. A prerequisite or
    command that names a variable the expansion cannot settle is skipped and
    clears the resolved flag.

    Returns
    -------
    Successors
        The reachable targets in discovery order, and whether none were skipped.
    """
    seen: list[str] = []
    is_resolved = True
    pending = collections.deque([LINT_TARGET])
    while pending:
        target = pending.popleft()
        if target in seen or target not in index:
            continue
        seen.append(target)
        for rule in index[target]:
            found = _rule_successors(rule, values)
            pending.extend(found.names)
            is_resolved = is_resolved and found.is_resolved
    return Successors(seen, is_resolved)


def _recipe_lines(
    target: str, rule: Rule, values: cabc.Mapping[str, str]
) -> list[RecipeLine]:
    """Return the expanded lines of one rule's recipe.

    A line counts as guarded when any recipe line of its rule turns a `MISSED`
    row into a failing status: interrogate rounds its percentage to one decimal,
    so the threshold alone passes a lone gap in a large repository.

    Returns
    -------
    list[RecipeLine]
        The lines, raw and expanded, in file order.
    """
    expanded = [(number, text, expand(text, values)) for number, text in rule.recipes]
    is_guarded = has_missed_guard("\n".join(item.text for _n, _t, item in expanded))
    return [
        RecipeLine(target, number, text, item, is_guarded)
        for number, text, item in expanded
    ]


def _interrogate_runs(
    index: RuleIndex, reachable: list[str], values: cabc.Mapping[str, str]
) -> list[InterrogateRun]:
    """Return every invocation in the recipes of the reachable targets.

    Returns
    -------
    list[InterrogateRun]
        The invocations in target and line order.
    """
    return [
        run
        for target in reachable
        for rule in index[target]
        for line in _recipe_lines(target, rule, values)
        for run in runs_in(line)
    ]


def _elsewhere(index: RuleIndex, reachable: list[str]) -> list[str]:
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
            _NAMES_TOOL.search(text) for rule in rules for _line, text in rule.recipes
        )
    )


def _analyse_makefile(
    makefile: pathlib.Path, inspect: MakefileInspector
) -> MakefileAnalysis:
    """Read the root Makefile and find the interrogate runs `lint` reaches.

    Returns
    -------
    MakefileAnalysis
        The path, the walk from `lint`, and the runs found along it.
    """
    report = inspect(makefile).report
    index = _rules_by_target(report)
    values = single_assignments(report["variables"])
    walk = _reachable(index, values)
    return MakefileAnalysis(
        path=str(report.get("source", {}).get("path", "Makefile")),
        has_lint_target=LINT_TARGET in index,
        reachable=walk.names,
        is_resolved=walk.is_resolved,
        elsewhere=_elsewhere(index, walk.names),
        runs=_interrogate_runs(index, walk.names, values),
    )


def _decode_config(
    path: pathlib.Path, relative: str
) -> tuple[dict[str, object] | None, str | None]:
    """Decode one configuration file, returning its table or the error.

    Returns
    -------
    tuple[dict[str, object] | None, str | None]
        The `[tool.interrogate]` table (empty when absent), or the error that
        stopped decoding.
    """
    if not relative.endswith(".toml"):
        return None, "not a TOML file; the policy reads only TOML configuration"
    try:
        parsed = tomllib.loads(read_text(path, OPERATION_READ_CONFIG))
    except UnicodeDecodeError as error:
        return None, f"not UTF-8 text: {error}"
    except tomllib.TOMLDecodeError as error:
        return None, f"invalid TOML: {error}"
    tool = parsed.get("tool")
    table = tool.get("interrogate") if isinstance(tool, dict) else None
    return (dict(table) if isinstance(table, dict) else {}), None


def _load_config(
    checkout: pathlib.Path, root: pathlib.Path, relative: str
) -> InterrogateConfig | None:
    """Return the `[tool.interrogate]` table of one file, if it exists.

    A file that is not TOML (an `.ini` file) is carried with an error: the
    policy cannot read its threshold, so it reports indeterminate.

    Returns
    -------
    InterrogateConfig | None
        The table (empty when the file has none), or ``None`` for no file.
    """
    path = checkout / relative
    if not within_checkout(root, path, OPERATION_READ_CONFIG):
        return None
    if not is_file(path, OPERATION_READ_CONFIG):
        return None
    table, error = _decode_config(path, relative)
    return {"path": relative, "table": table, "error": error}


def _is_readable_config(run: InterrogateRun) -> bool:
    """Return whether a run names a configuration file the builder can open."""
    return bool(run["config"]) and run["resolved"] and "$" not in str(run["config"])


def _load_configs(
    checkout: pathlib.Path, root: pathlib.Path, runs: list[InterrogateRun]
) -> list[InterrogateConfig]:
    """Return `pyproject.toml` and every file a run names with `--config`.

    Returns
    -------
    list[InterrogateConfig]
        One fact per file that exists, the default first.
    """
    named = {str(run["config"]) for run in runs if _is_readable_config(run)}
    names = [PYPROJECT_FILENAME, *sorted(named - {PYPROJECT_FILENAME})]
    loaded = (_load_config(checkout, root, name) for name in names)
    return [config for config in loaded if config is not None]


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
    for current, directories, files in os.walk(checkout, onerror=raise_walk_error):
        directories[:] = sorted(
            name for name in directories if name not in PYTHON_PRUNED_DIRECTORIES
        )
        base = pathlib.Path(current)
        found.extend(
            (base / name).relative_to(checkout).as_posix()
            for name in files
            if name.endswith(".py")
            and not is_symlink(base / name, OPERATION_SCAN_PYTHON)
        )
    return sorted(found)


def _has_root_makefile(checkout: pathlib.Path, root: pathlib.Path) -> bool:
    """Return whether the checkout has a regular root `Makefile` inside it."""
    makefile = checkout / "Makefile"
    return within_checkout(root, makefile, OPERATION_READ_MAKEFILE) and is_file(
        makefile, OPERATION_PROBE_PATH
    )


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
    has_makefile = _has_root_makefile(checkout, root)
    analysis = (
        _analyse_makefile(checkout / "Makefile", inspect)
        if has_makefile
        else MakefileAnalysis()
    )
    configs = _load_configs(checkout, root, analysis.runs)
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
        "makefile_path": analysis.path,
        "lint_target": analysis.has_lint_target,
        "reachable_targets": analysis.reachable,
        "reachability_resolved": analysis.is_resolved,
        "interrogate_elsewhere": analysis.elsewhere,
        "runs": analysis.runs,
        "configs": configs,
        "python_files": python_files,
    }
