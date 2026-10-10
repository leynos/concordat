"""Build the `policy-input/uv-gate-baseline` envelope.

The uv gate rule package reasons over the facts that decide whether a checkout
reaches `uv` only through the vendored `scripts/uv_gate.py` helper: the root
`Makefile` (parsed by the pinned `makeutil`, as the spelling, Markdown and Rust
envelopes do), every GitHub Actions workflow and composite action, the helper's
own SHA-256 digest, `pyproject.toml` with the requirement strings and Git
sources it declares, and whether `uv.lock` exists. Each fact is recorded as it
was found: a file that exists but cannot be decoded is carried with its
`error`, so the policy reports an indeterminate verdict rather than reading the
file as absent.

The file readers are `markdown_envelope`'s, so every envelope shares one
containment guard: no policy input is read from outside the checkout.
"""

from __future__ import annotations

import hashlib
import pathlib
import tomllib
import typing as typ

from concordat.errors import OperationalRuleError

from .makefile_facts import MakefileInspector, inspect_makefile
from .markdown_envelope import (
    OPERATION_PROBE_PATH,
    OPERATION_READ_MAKEFILE,
    Repository,
    WorkflowFile,
    _is_dir,
    _load_workflow,
    _read_text,
    is_file,
    load_workflows,
    resolved_root,
    within_checkout,
)

if typ.TYPE_CHECKING:
    from .makefile_facts import MakeutilReport

ENVELOPE_SCHEMA_VERSION: typ.Final = 1
ENVELOPE_KIND: typ.Final = "policy-input/uv-gate-baseline"
GATE_PATH: typ.Final = "scripts/uv_gate.py"
PYPROJECT_FILENAME: typ.Final = "pyproject.toml"
UV_LOCK_FILENAME: typ.Final = "uv.lock"
ACTIONS_DIRECTORY: typ.Final = pathlib.PurePosixPath(".github/actions")
ACTION_FILENAMES: typ.Final = ("action.yml", "action.yaml")

OPERATION_READ_GATE: typ.Final = "read-uv-gate"
OPERATION_READ_PYPROJECT: typ.Final = "read-pyproject"
OPERATION_LIST_ACTIONS: typ.Final = "list-actions"


class GateFile(typ.TypedDict):
    """The vendored helper: its digest, or the error that prevented one."""

    path: str
    sha256: str | None
    error: str | None


class Requirement(typ.TypedDict):
    """One requirement string and the pyproject table that declares it."""

    origin: str
    spec: str


class GitSource(typ.TypedDict):
    """One `[tool.uv.sources]` Git entry, with the revision selectors it has."""

    name: str
    git: str
    rev: str | None
    tag: str | None
    branch: str | None


class PyprojectFile(typ.TypedDict):
    """`pyproject.toml` decoded, or carrying its error."""

    path: str
    parsed: object | None
    error: str | None


class UvGateApplicability(typ.TypedDict):
    """Evidence that the uv gate rule package may apply."""

    root_makefile: bool
    pyproject: bool
    uv_lock: bool
    gate_file: bool


class UvGateEnvelope(typ.TypedDict):
    """The `policy-input/uv-gate-baseline` document."""

    schema_version: int
    kind: str
    repository: Repository
    applicability: UvGateApplicability
    makefile: MakeutilReport | None
    workflows: list[WorkflowFile]
    actions: list[WorkflowFile]
    gate: GateFile | None
    pyproject: PyprojectFile | None
    requirements: list[Requirement]
    git_sources: list[GitSource]


def _is_contained_file(root: pathlib.Path, path: pathlib.Path, operation: str) -> bool:
    """Return whether *path* is a regular file resolving inside the checkout.

    An `OperationalRuleError` propagates from the containment guard when the
    path escapes the checkout.

    Returns
    -------
    bool
        Whether the path exists, stays inside the checkout, and is a file.
    """
    return within_checkout(root, path, operation) and is_file(path, operation)


def _load_gate(checkout: pathlib.Path, root: pathlib.Path) -> GateFile | None:
    """Return the helper's digest, or ``None`` when it is absent.

    The digest is of the bytes on disk, which is what "byte-for-byte" means: a
    re-encoded or re-wrapped copy has a different digest and is drift.

    Returns
    -------
    GateFile | None
        The path and SHA-256, or ``None`` when the helper is absent.

    Raises
    ------
    OperationalRuleError
        If the helper exists but cannot be read.
    """
    path = checkout / GATE_PATH
    if not _is_contained_file(root, path, OPERATION_READ_GATE):
        return None
    try:
        data = path.read_bytes()
    except OSError as error:
        message = f"cannot read {path}: {error}"
        raise OperationalRuleError(
            message, operation=OPERATION_READ_GATE, resource=path
        ) from error
    return {
        "path": GATE_PATH,
        "sha256": hashlib.sha256(data).hexdigest(),
        "error": None,
    }


def _load_pyproject(checkout: pathlib.Path, root: pathlib.Path) -> PyprojectFile | None:
    """Return the decoded `pyproject.toml`, or ``None`` when it is absent.

    Returns
    -------
    PyprojectFile | None
        The file decoded as TOML, or carrying its decoding error.
    """
    path = checkout / PYPROJECT_FILENAME
    if not _is_contained_file(root, path, OPERATION_READ_PYPROJECT):
        return None
    fact: PyprojectFile = {"path": PYPROJECT_FILENAME, "parsed": None, "error": None}
    try:
        fact["parsed"] = tomllib.loads(_read_text(path, OPERATION_READ_PYPROJECT))
    except UnicodeDecodeError as error:
        fact["error"] = f"not UTF-8 text: {error}"
    except tomllib.TOMLDecodeError as error:
        fact["error"] = f"invalid TOML: {error}"
    return fact


def _table(value: object) -> dict[str, object]:
    """Return *value* when it is a table, else an empty one."""
    if isinstance(value, dict):
        return {str(key): item for key, item in value.items()}
    return {}


def _strings(value: object) -> list[str]:
    """Return the string items of *value* when it is a list, else nothing."""
    return (
        [item for item in value if isinstance(item, str)]
        if isinstance(value, list)
        else []
    )


def _requirements(parsed: object) -> list[Requirement]:
    """Return every requirement string a `pyproject.toml` declares.

    Covers `[project]` dependencies and optional dependencies, PEP 735
    `[dependency-groups]`, `[build-system]` requirements and the legacy
    `[tool.uv] dev-dependencies`. Include tables inside a dependency group are
    not requirement strings and are skipped.

    Returns
    -------
    list[Requirement]
        The requirement strings in declaration order, each with its origin.
    """
    document = _table(parsed)
    project = _table(document.get("project"))
    found: list[Requirement] = [
        {"origin": "project.dependencies", "spec": spec}
        for spec in _strings(project.get("dependencies"))
    ]
    for extra, specs in _table(project.get("optional-dependencies")).items():
        found.extend(
            {"origin": f"project.optional-dependencies.{extra}", "spec": spec}
            for spec in _strings(specs)
        )
    for group, specs in _table(document.get("dependency-groups")).items():
        found.extend(
            {"origin": f"dependency-groups.{group}", "spec": spec}
            for spec in _strings(specs)
        )
    found.extend(
        {"origin": "build-system.requires", "spec": spec}
        for spec in _strings(_table(document.get("build-system")).get("requires"))
    )
    uv_table = _table(_table(document.get("tool")).get("uv"))
    found.extend(
        {"origin": "tool.uv.dev-dependencies", "spec": spec}
        for spec in _strings(uv_table.get("dev-dependencies"))
    )
    return found


def _optional_string(table: dict[str, object], key: str) -> str | None:
    """Return ``table[key]`` when it is a string, else ``None``."""
    value = table.get(key)
    return value if isinstance(value, str) else None


def _git_sources(parsed: object) -> list[GitSource]:
    """Return every `[tool.uv.sources]` entry that names a Git repository.

    A source may be a list of entries selected by marker; each is reported.

    Returns
    -------
    list[GitSource]
        One fact per Git source, with the revision selectors it declares.
    """
    uv_table = _table(_table(_table(parsed).get("tool")).get("uv"))
    found: list[GitSource] = []
    for name, entry in _table(uv_table.get("sources")).items():
        entries = entry if isinstance(entry, list) else [entry]
        for candidate in entries:
            source = _table(candidate)
            git = _optional_string(source, "git")
            if git is None:
                continue
            found.append({
                "name": name,
                "git": git,
                "rev": _optional_string(source, "rev"),
                "tag": _optional_string(source, "tag"),
                "branch": _optional_string(source, "branch"),
            })
    return found


def load_actions(checkout: pathlib.Path, root: pathlib.Path) -> list[WorkflowFile]:
    """Return every composite action file under `.github/actions`, sorted.

    Only the `action.yml` or `action.yaml` directly inside each action's
    directory counts. An `OperationalRuleError` propagates from the containment
    guard, or when the directory exists but cannot be listed.

    Returns
    -------
    list[WorkflowFile]
        One fact per action file, ordered by directory name.

    Raises
    ------
    OperationalRuleError
        If the actions directory exists but cannot be listed.
    """
    directory = checkout / ACTIONS_DIRECTORY
    if not within_checkout(root, directory, OPERATION_LIST_ACTIONS):
        return []
    if not _is_dir(directory, OPERATION_LIST_ACTIONS):
        return []
    try:
        entries = sorted(directory.iterdir(), key=lambda entry: entry.name)
    except OSError as error:
        message = f"cannot list {directory}: {error}"
        raise OperationalRuleError(
            message, operation=OPERATION_LIST_ACTIONS, resource=directory
        ) from error
    return [
        _load_workflow(checkout, root, ACTIONS_DIRECTORY / entry.name / name)
        for entry in entries
        for name in ACTION_FILENAMES
        if is_file(entry / name, OPERATION_LIST_ACTIONS)
    ]


def build_uv_gate_envelope(
    checkout: pathlib.Path, *, inspect: MakefileInspector = inspect_makefile
) -> UvGateEnvelope:
    """Assemble the uv gate policy input for one local checkout.

    Parameters
    ----------
    checkout:
        Path to the checkout under audit.
    inspect:
        Reads the root Makefile's facts. The default is the pure query; the
        command boundary injects an observing one.

    An `OperationalRuleError` propagates from the fact readers if the root
    `Makefile` cannot be parsed by `makeutil`, a fact file exists but cannot
    be opened, or a fact file resolves to a location outside the checkout.

    Returns
    -------
    UvGateEnvelope
        The policy input document assembled from the checkout.
    """
    root = resolved_root(checkout)
    makefile_report: MakeutilReport | None = None
    if _is_contained_file(root, checkout / "Makefile", OPERATION_READ_MAKEFILE):
        makefile_report = inspect(checkout / "Makefile").report
    gate = _load_gate(checkout, root)
    pyproject = _load_pyproject(checkout, root)
    parsed = pyproject["parsed"] if pyproject is not None else None
    return {
        "schema_version": ENVELOPE_SCHEMA_VERSION,
        "kind": ENVELOPE_KIND,
        "repository": {"path": str(checkout), "name": None},
        "applicability": {
            "root_makefile": makefile_report is not None,
            "pyproject": pyproject is not None,
            "uv_lock": _is_contained_file(
                root, checkout / UV_LOCK_FILENAME, OPERATION_PROBE_PATH
            ),
            "gate_file": gate is not None,
        },
        "makefile": makefile_report,
        "workflows": load_workflows(checkout, root),
        "actions": load_actions(checkout, root),
        "gate": gate,
        "pyproject": pyproject,
        "requirements": _requirements(parsed),
        "git_sources": _git_sources(parsed),
    }
