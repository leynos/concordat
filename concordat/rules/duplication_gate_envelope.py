"""Build the `policy-input/python-duplication-gate` envelope.

The shared `df12-duplication` command validates the `[tool.nose]` table's shape
but cannot know a repository's policy: whether the scan may be capped, whether
test scaffolding is kept out of it, and whether every root still selects
something. This envelope records the facts the policy needs to decide those:
`pyproject.toml` decoded with `tomllib`, the settings read from its
`[tool.nose]` table, and, for each scan root, which Python files the scan would
select and which of those sit in a test tree.

Selection follows the detector: the `exclude` patterns are gitignore-style and
are matched against each file's path relative to the scan root they apply to
(measured against nose 0.20.0: `unittests/**` with the root `cuprum` excludes
`cuprum/unittests`). The matcher is `pathspec`'s `GitIgnoreSpec`, so `**` and
a trailing `/` mean what git means by them. nose treats every entry as a plain
exclusion, so a leading `!` or `#` is literal here, not negation or a comment.
Hidden files and directories are skipped, as nose skips them, and nothing else
is pruned by name. Only `[tool.nose].exclude` is read; nose also honours
`.gitignore` files inside a root, which this envelope does not model, so a
test tree that is hidden only by one reads as selected (a clean checkout holds
no gitignored files, so this matters only for a dirty working tree).

The file readers are `markdown_envelope`'s, so every envelope shares one
containment guard: no policy input is read from outside the checkout.
"""

from __future__ import annotations

import dataclasses as dc
import os
import pathlib
import tomllib
import typing as typ

import pathspec
from pathspec.patterns.gitignore.base import GitIgnorePatternError

from .markdown_envelope import (
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

ENVELOPE_SCHEMA_VERSION: typ.Final = 1
ENVELOPE_KIND: typ.Final = "policy-input/python-duplication-gate"
PYPROJECT_FILENAME: typ.Final = "pyproject.toml"
PYTHON_SUFFIXES: typ.Final = (".py", ".pyi")
DEFAULT_TEST_DIRECTORY_NAMES: typ.Final = ("tests", "test", "unittests", "testing")
# A repository with a thousand selected test files needs one finding, not a
# thousand lines of evidence; the count carries the rest.
MAX_LISTED_TEST_FILES: typ.Final = 10

OPERATION_READ_PYPROJECT: typ.Final = "read-pyproject"
OPERATION_SCAN_PYTHON: typ.Final = "scan-python-files"


class PyprojectFact(typ.TypedDict):
    """`pyproject.toml` decoded, or carrying its error."""

    path: str
    error: str | None


class NoseFact(typ.TypedDict):
    """The `[tool.nose]` settings the policy judges."""

    top_present: bool
    top: int | None
    roots: list[str]
    exclude: list[str]
    problems: list[str]


class RootFact(typ.TypedDict):
    """What one scan root selects."""

    root: str
    exists: bool
    selected_count: int
    test_file_count: int
    test_files: list[str]


class DuplicationGateApplicability(typ.TypedDict):
    """Evidence that the rule applies."""

    pyproject: bool
    nose_table: bool


class DuplicationGateEnvelope(typ.TypedDict):
    """The `policy-input/python-duplication-gate` document."""

    schema_version: int
    kind: str
    repository: Repository
    applicability: DuplicationGateApplicability
    test_directory_names: list[str]
    pyproject: PyprojectFact | None
    nose: NoseFact | None
    roots: list[RootFact]


def _load_pyproject(
    checkout: pathlib.Path, root: pathlib.Path
) -> tuple[PyprojectFact, dict[str, object]] | None:
    """Return the fact and decoded `pyproject.toml`, or ``None`` when absent.

    Returns
    -------
    tuple[PyprojectFact, dict[str, object]] | None
        The fact, and the decoded document (empty when it could not be
        decoded), or ``None`` when the file is absent.
    """
    path = checkout / PYPROJECT_FILENAME
    if not (
        within_checkout(root, path, OPERATION_READ_PYPROJECT)
        and is_file(path, OPERATION_READ_PYPROJECT)
    ):
        return None
    fact: PyprojectFact = {"path": PYPROJECT_FILENAME, "error": None}
    try:
        parsed = tomllib.loads(read_text(path, OPERATION_READ_PYPROJECT))
    except UnicodeDecodeError as error:
        fact["error"] = f"not UTF-8 text: {error}"
        return fact, {}
    except tomllib.TOMLDecodeError as error:
        fact["error"] = f"invalid TOML: {error}"
        return fact, {}
    return fact, parsed


def _nose_table(parsed: dict[str, object]) -> object:
    """Return the `[tool.nose]` value, or ``None`` when there is none."""
    tool = parsed.get("tool")
    return tool.get("nose") if isinstance(tool, dict) else None


def _string_list(value: object) -> list[str] | None:
    """Return *value* when it is a list of strings, else ``None``."""
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return typ.cast("list[str]", value)
    return None


def _root_problem(root: str) -> str | None:
    """Return why *root* cannot name a path inside the checkout, if it cannot."""
    path = pathlib.PurePosixPath(root)
    if not root.strip():
        return "a scan root is empty"
    if path.is_absolute() or ".." in path.parts:
        return f"scan root {root!r} is absolute or leaves the repository"
    return None


def _exclusion_spec(patterns: list[str]) -> pathspec.GitIgnoreSpec:
    """Compile *patterns* as plain exclusions, as the detector reads them.

    nose passes every `exclude` entry through as an exclusion, so `!` does not
    negate (measured on nose 0.20.0: `["**", "!m.py"]` selects nothing) and `#`
    does not start a comment. gitignore syntax gives both meaning, so a leading
    one is escaped to be the literal character it is for the detector.

    Returns
    -------
    pathspec.GitIgnoreSpec
        The compiled patterns. A pattern that is not valid gitignore syntax
        raises pathspec's `GitIgnorePatternError`.
    """
    literal = ("!", "#")
    return pathspec.GitIgnoreSpec.from_lines([
        f"\\{pattern}" if pattern.startswith(literal) else pattern
        for pattern in patterns
    ])


def _exclude_problem(patterns: list[str]) -> str | None:
    """Return why the patterns are not valid gitignore patterns, if they are not."""
    try:
        _exclusion_spec(patterns)
    except GitIgnorePatternError as error:
        return f"[tool.nose].exclude holds an invalid pattern: {error}"
    return None


def _top_fact(table: dict[str, object]) -> tuple[bool, int | None, list[str]]:
    """Return whether `top` is set, its integer value, and any shape problem."""
    if "top" not in table:
        return False, None, []
    value = table["top"]
    if isinstance(value, int) and not isinstance(value, bool):
        return True, value, []
    return True, None, [f"[tool.nose].top must be an integer, not {value!r}"]


def _roots_fact(table: dict[str, object]) -> tuple[list[str], list[str]]:
    """Return the scan roots, and the problems that make them unusable."""
    roots = _string_list(table.get("roots"))
    if not roots:
        return [], ["[tool.nose].roots must be a non-empty list of strings"]
    return roots, [problem for root in roots if (problem := _root_problem(root))]


def _exclude_fact(table: dict[str, object]) -> tuple[list[str], list[str]]:
    """Return the exclude patterns, and the problems that make them unusable."""
    exclude = _string_list(table.get("exclude", []))
    if exclude is None:
        return [], ["[tool.nose].exclude must be a list of strings"]
    problem = _exclude_problem(exclude)
    return exclude, [problem] if problem else []


def _nose_fact(table: object) -> NoseFact:
    """Read the `[tool.nose]` settings, recording each shape problem.

    Returns
    -------
    NoseFact
        The settings, with `problems` naming everything that could not be read.
    """
    fact: NoseFact = {
        "top_present": False,
        "top": None,
        "roots": [],
        "exclude": [],
        "problems": [],
    }
    if not isinstance(table, dict):
        fact["problems"].append("[tool.nose] must be a table")
        return fact
    settings = {str(key): value for key, value in table.items()}
    fact["top_present"], fact["top"], top_problems = _top_fact(settings)
    fact["roots"], root_problems = _roots_fact(settings)
    fact["exclude"], exclude_problems = _exclude_fact(settings)
    fact["problems"] = [*top_problems, *root_problems, *exclude_problems]
    return fact


def _is_python_name(name: str) -> bool:
    """Return whether *name* is a Python source or stub file the scan reads."""
    return not name.startswith(".") and name.endswith(PYTHON_SUFFIXES)


def _python_files(base: pathlib.Path) -> cabc.Iterator[pathlib.PurePosixPath]:
    """Yield the Python files under directory *base*, relative to *base*.

    The walk never follows symbolic links and skips a link to a file. Hidden
    directories and files are skipped, as the detector skips them; nothing else
    is pruned by name, because the detector does not (measured on nose 0.20.0:
    `vendor`, `venv` and `node_modules` are scanned unless a `.gitignore` or an
    `exclude` pattern says otherwise). A directory that cannot be listed raises
    rather than reading as one with no Python in it.

    Yields
    ------
    pathlib.PurePosixPath
        Each Python file's path below *base*.
    """
    for current, directories, files in os.walk(base, onerror=raise_walk_error):
        directories[:] = sorted(
            name for name in directories if not name.startswith(".")
        )
        here = pathlib.Path(current)
        for name in sorted(files):
            if _is_python_name(name) and not is_symlink(
                here / name, OPERATION_SCAN_PYTHON
            ):
                yield pathlib.PurePosixPath((here / name).relative_to(base).as_posix())


@dc.dataclass(frozen=True, slots=True)
class _Scope:
    """What the selection of one checkout is judged against."""

    checkout: pathlib.Path
    resolved: pathlib.Path
    spec: pathspec.GitIgnoreSpec
    test_names: frozenset[str]


def _selected_files(scope: _Scope, root_name: str) -> list[str] | None:
    """Return the checkout-relative files the scan selects from one root.

    Returns
    -------
    list[str] | None
        The selected Python files, or ``None`` when the root does not exist.
        A root that is itself a Python file selects it unless an exclude
        pattern names its file name.
    """
    path = scope.checkout / root_name
    if not within_checkout(scope.resolved, path, OPERATION_SCAN_PYTHON):
        return None
    prefix = pathlib.PurePosixPath(root_name)
    # `within_checkout` has already proved the path exists and answered for any
    # refusal, so this is a plain kind test, not a probe that could hide one.
    if path.is_file():
        is_selected = _is_python_name(path.name) and not scope.spec.match_file(
            path.name
        )
        return [prefix.as_posix()] if is_selected else []
    return [
        (prefix / relative).as_posix()
        for relative in _python_files(path)
        if not scope.spec.match_file(relative.as_posix())
    ]


def _is_in_test_tree(file: str, names: cabc.Collection[str]) -> bool:
    """Return whether any directory above *file* has one of the test names."""
    return any(part in names for part in pathlib.PurePosixPath(file).parts[:-1])


def _root_fact(scope: _Scope, root_name: str) -> RootFact:
    """Return what the scan selects from one root.

    A file counts as being in a test tree by its checkout-relative path, so a
    root that is itself a test tree, or lies inside one, selects test files.

    Returns
    -------
    RootFact
        The root's selection and the selected files that are test files.
    """
    selected = _selected_files(scope, root_name)
    tests = [f for f in selected or [] if _is_in_test_tree(f, scope.test_names)]
    return {
        "root": root_name,
        "exists": selected is not None,
        "selected_count": len(selected or []),
        "test_file_count": len(tests),
        "test_files": tests[:MAX_LISTED_TEST_FILES],
    }


def _test_directory_names(parameters: cabc.Mapping[str, object] | None) -> list[str]:
    """Return the manifest's test directory names, or the defaults."""
    declared = _string_list((parameters or {}).get("test_directory_names"))
    return declared if declared is not None else list(DEFAULT_TEST_DIRECTORY_NAMES)


def build_duplication_gate_envelope(
    checkout: pathlib.Path,
    parameters: cabc.Mapping[str, object] | None = None,
) -> DuplicationGateEnvelope:
    """Assemble the duplication gate policy input for one local checkout.

    Parameters
    ----------
    checkout:
        Path to the checkout under audit.
    parameters:
        The manifest parameters; `test_directory_names` names the directories
        that hold test scaffolding.

    An `OperationalRuleError` propagates if `pyproject.toml` or a scanned
    directory cannot be opened or listed, or a path resolves outside the
    checkout.

    Returns
    -------
    DuplicationGateEnvelope
        The policy input document assembled from the checkout. Roots are
        recorded only when `[tool.nose]` has no shape problem.
    """
    root = resolved_root(checkout)
    names = _test_directory_names(parameters)
    loaded = _load_pyproject(checkout, root)
    table = _nose_table(loaded[1]) if loaded is not None else None
    nose = _nose_fact(table) if table is not None else None
    roots: list[RootFact] = []
    if nose is not None and not nose["problems"]:
        scope = _Scope(
            checkout,
            root,
            _exclusion_spec(nose["exclude"]),
            frozenset(names),
        )
        roots = [_root_fact(scope, name) for name in nose["roots"]]
    return {
        "schema_version": ENVELOPE_SCHEMA_VERSION,
        "kind": ENVELOPE_KIND,
        "repository": {"path": str(checkout), "name": None},
        "applicability": {
            "pyproject": loaded is not None,
            "nose_table": table is not None,
        },
        "test_directory_names": names,
        "pyproject": loaded[0] if loaded is not None else None,
        "nose": nose,
        "roots": roots,
    }
