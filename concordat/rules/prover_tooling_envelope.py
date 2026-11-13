"""Build the facts for the `rust-prover-tooling` rule (PT-001 to PT-005).

A repository that uses Kani or Verus is held to one standard: it pins
`rust-prover-tools` to a single commit, installs provers in continuous
integration only through the shared `install-prover-tools` action (pinned by
SHA), and carries no local installer scripts or tests of prover installation.
A named exception is recorded as data in `.concordat`.

This builder reads the facts the policy needs, each from a parser and never
from a text search:

- the root `Makefile`, through the pinned `makeutil`, for its rules (so a CI
  step that runs `make X` can be followed through X's prerequisites) and for
  the `rust-prover-tools` revisions its variables name;
- each root workflow's decoded document, for its steps' `uses` and for the
  commands in its `run` scripts, split with `shlex` and classified in
  `prover_commands`;
- `tools/rust-prover-tools/REF`, `tools/kani/VERSION` and
  `tools/verus/VERSION`;
- shell scripts (commands, as for workflows) and Python scripts (string
  constants, from the `ast`), under the script directories;
- file names under `tests/`;
- the exceptions declared in `.concordat`, with whether each one's decision
  record exists.

A workflow, script or Makefile that cannot be read stays a fact with its
reason, so the policy answers indeterminate rather than reporting a broken
checkout as compliant.
"""

from __future__ import annotations

import ast
import dataclasses
import pathlib
import typing as typ

from concordat.errors import OperationalRuleError

from .codescene_coverage_envelope import _load_workflows
from .fs_probe import regular_file_exists
from .makefile_facts import (
    MakefileInspector,
    MakefileRefusedError,
    MakeutilReport,
    inspect_makefile,
)
from .markdown_envelope import is_file, resolved_root, within_checkout
from .nested_cargo import origin_slug
from .prover_commands import commands, is_installer, is_runner
from .rust_surfaces import CONCORDAT_FILENAME, rust_language_mapping

if typ.TYPE_CHECKING:
    from .codescene_coverage_envelope import Repository, WorkflowFile

ENVELOPE_SCHEMA_VERSION: typ.Final = 1
ENVELOPE_KIND: typ.Final = "policy-input/rust-prover-tooling"
OPERATION: typ.Final = "read-prover-tooling-facts"
SHARED_ACTION: typ.Final = "leynos/shared-actions/.github/actions/install-prover-tools"
REF_FILE: typ.Final = "tools/rust-prover-tools/REF"
VERSION_FILES: typ.Final = ("tools/kani/VERSION", "tools/verus/VERSION")
SCRIPT_DIRECTORIES: typ.Final = ("scripts", ".github/scripts", "tools")
TEST_DIRECTORY: typ.Final = "tests"
SKIPPED_DIRECTORIES: typ.Final = frozenset({
    "target",
    "node_modules",
    ".git",
    ".venv",
    "fixtures",
    "fixture",
    "tests",
    "test",
})
INSTALL_WORDS: typ.Final = ("install", "setup", "bootstrap")
TOOL_WORDS: typ.Final = ("kani", "verus", "z3")
TEST_TOOL_WORDS: typ.Final = (*TOOL_WORDS, "prover")
DOWNLOAD_PREFIXES: typ.Final = (
    "https://github.com/model-checking/kani",
    "https://github.com/verus-lang/verus",
)
PIN_MARKER: typ.Final = "rust-prover-tools@"
REVISION_LENGTH: typ.Final = 40
HEX_DIGITS: typ.Final = frozenset("0123456789abcdef")


class Pin(typ.TypedDict):
    """Store one `rust-prover-tools` revision the checkout names.

    Attributes
    ----------
    value:
        The 40-hex revision.
    source:
        The file it was read from.
    """

    value: str
    source: str


class InstallerStep(typ.TypedDict):
    """Store one workflow step that installs a prover itself.

    Attributes
    ----------
    where:
        `<job>/<step index>`.
    command:
        The installer command as written, or the `make` target whose
        prerequisites reach one.
    """

    where: str
    command: str


class SharedUse(typ.TypedDict):
    """Store one use of the shared install action.

    Attributes
    ----------
    where:
        `<job>/<step index>`.
    ref:
        The text after the `@`, which should be a 40-hex SHA.
    """

    where: str
    ref: str


class WorkflowFacts(typ.TypedDict):
    """Store one workflow's prover facts, or the reason it could not be read.

    Attributes
    ----------
    path:
        The workflow's path relative to the checkout, in POSIX form.
    error:
        Why the workflow could not be decoded, or None.
    calls_make:
        Whether any `run` script calls `make`.
    installer_steps:
        Steps that install a prover, directly or through a Make prerequisite.
    shared_uses:
        Every use of the shared install action.
    """

    path: str
    error: str | None
    calls_make: bool
    installer_steps: list[InstallerStep]
    shared_uses: list[SharedUse]


class LocalInstaller(typ.TypedDict):
    """Store one file that installs a prover outside the shared action.

    Attributes
    ----------
    path:
        The file, relative to the checkout.
    reason:
        `name`, `command` or `download`.
    """

    path: str
    reason: str


class ProverException(typ.TypedDict):
    """Store one declared exception and the facts it is checked against.

    Attributes
    ----------
    clauses:
        The check identifiers the exception waives.
    pin:
        The revision (or its prefix) the repository stays on.
    record:
        The decision record's path.
    rationale:
        Why the repository stays.
    review_trigger:
        The event that sends it back for review.
    record_present:
        Whether the decision record is a regular file in the checkout.
    """

    clauses: list[str]
    pin: str
    record: str
    rationale: str
    review_trigger: str
    record_present: bool


class MakefileState(typ.TypedDict):
    """Store whether the Makefile was read.

    Attributes
    ----------
    state:
        `absent`, `ok` or `refused`.
    error:
        The refusal reason, or None.
    """

    state: str
    error: str | None


class ProverToolingEnvelope(typ.TypedDict):
    """Store the evidence evaluated by the `rust-prover-tooling` rule.

    Attributes
    ----------
    schema_version:
        The envelope's schema version, which the policy checks.
    kind:
        The policy-input identifier, which the policy checks.
    repository:
        The checkout's path and, where the origin names one, its GitHub slug.
    makefile:
        Whether the root Makefile was read.
    version_files:
        The prover version pins present (`tools/kani/VERSION` and so on).
    pins:
        Every `rust-prover-tools` revision named, with its source.
    runs_prover:
        Whether any Makefile recipe, workflow step or script installs or runs a
        prover.
    workflows:
        Every root workflow's prover facts.
    local_installers:
        Files that install a prover outside the shared action.
    install_tests:
        Test files whose names say they test prover installation.
    unreadable:
        Files that could not be read, each with its reason.
    exceptions:
        The exceptions declared in `.concordat`.
    """

    schema_version: int
    kind: str
    repository: Repository
    makefile: MakefileState
    version_files: list[str]
    pins: list[Pin]
    runs_prover: bool
    workflows: list[WorkflowFacts]
    local_installers: list[LocalInstaller]
    install_tests: list[str]
    unreadable: list[str]
    exceptions: list[ProverException]


@dataclasses.dataclass(frozen=True, slots=True)
class MakeRules:
    """The Makefile's rules, flattened: prerequisites and recipe commands."""

    prerequisites: dict[str, list[str]]
    recipes: dict[str, list[list[str]]]

    def closure(self, targets: list[str]) -> list[str]:
        """Return *targets* and every prerequisite they reach.

        Returns
        -------
        list[str]
            Each reachable target that has a rule, once.
        """
        seen: list[str] = []
        todo = list(targets)
        while todo:
            target = todo.pop()
            if target in seen or target not in self.prerequisites:
                continue
            seen.append(target)
            todo.extend(self.prerequisites[target])
        return seen


def _is_revision(text: str) -> bool:
    """Return whether *text* is a 40-digit lowercase hexadecimal revision."""
    return len(text) == REVISION_LENGTH and set(text) <= HEX_DIGITS


def _revision_after_marker(text: str) -> str | None:
    """Return the revision following `rust-prover-tools@` in *text*, if literal."""
    if PIN_MARKER not in text:
        return None
    candidate = text.split(PIN_MARKER, maxsplit=1)[1][:REVISION_LENGTH]
    return candidate if _is_revision(candidate) else None


def _make_rules(report: MakeutilReport) -> MakeRules:
    """Flatten a `makeutil` report into prerequisites and recipe commands."""
    prerequisites: dict[str, list[str]] = {}
    recipes: dict[str, list[list[str]]] = {}
    for rule in report["rules"]:
        parsed = [
            argv for recipe in rule["recipes"] for argv in commands(recipe["text"])
        ]
        for target in rule["targets"]:
            prerequisites.setdefault(target, []).extend(rule["prerequisites"])
            recipes.setdefault(target, []).extend(parsed)
    return MakeRules(prerequisites, recipes)


def _makefile_pins(report: MakeutilReport) -> list[Pin]:
    """Return the literal revisions the Makefile's variables name."""
    found: list[Pin] = []
    for variable in typ.cast("list[dict[str, object]]", report["variables"]):
        raw = str(variable.get("raw_value", ""))
        name = str(variable.get("name", ""))
        revision = _revision_after_marker(raw)
        if (
            revision is None
            and name.startswith("PROVER_TOOLS")
            and _is_revision(raw.strip())
        ):
            revision = raw.strip()
        if revision is not None:
            found.append({"value": revision, "source": "Makefile"})
    return found


def _ref_pins(checkout: pathlib.Path) -> list[Pin]:
    """Return the revisions `tools/rust-prover-tools/REF` names."""
    path = checkout / REF_FILE
    if not regular_file_exists(path, operation=OPERATION):
        return []
    found: list[Pin] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        value = line.split(":", maxsplit=1)[-1].strip()
        if _is_revision(value):
            found.append({"value": value, "source": REF_FILE})
    return found


def _steps(job: object) -> list[dict[str, object]]:
    """Return a job's step mappings."""
    steps = job.get("steps") if isinstance(job, dict) else None
    return (
        [
            typ.cast("dict[str, object]", step)
            for step in steps
            if isinstance(step, dict)
        ]
        if isinstance(steps, list)
        else []
    )


def _make_targets(argv: list[str]) -> list[str]:
    """Return the targets a `make` command names."""
    return [word for word in argv[1:] if not word.startswith("-") and "=" not in word]


def _step_facts(
    where: str, step: dict[str, object], rules: MakeRules
) -> tuple[list[InstallerStep], list[SharedUse], bool, bool, list[Pin]]:
    """Return one step's installers, shared uses, make use, runs and pins."""
    installers: list[InstallerStep] = []
    shared: list[SharedUse] = []
    uses = step.get("uses")
    if isinstance(uses, str) and uses.startswith(f"{SHARED_ACTION}@"):
        shared.append({"where": where, "ref": uses.split("@", maxsplit=1)[1]})
    run = step.get("run")
    calls_make = False
    runs = False
    pins: list[Pin] = []
    for argv in commands(run) if isinstance(run, str) else []:
        revision = next(
            (r for word in argv if (r := _revision_after_marker(word)) is not None),
            None,
        )
        if revision is not None:
            pins.append({"value": revision, "source": "workflow"})
        runs = runs or is_installer(argv) or is_runner(argv)
        if is_installer(argv):
            installers.append({"where": where, "command": " ".join(argv)})
        if argv and argv[0] == "make":
            calls_make = True
            reached = rules.closure(_make_targets(argv))
            if any(is_installer(c) for t in reached for c in rules.recipes[t]):
                installers.append({"where": where, "command": " ".join(argv)})
            runs = runs or any(is_runner(c) for t in reached for c in rules.recipes[t])
    return installers, shared, calls_make, runs, pins


def _workflow_facts(
    workflow: WorkflowFile, rules: MakeRules
) -> tuple[WorkflowFacts, bool, list[Pin]]:
    """Return one workflow's facts, whether it runs a prover, and its pins."""
    facts: WorkflowFacts = {
        "path": workflow["path"],
        "error": workflow["error"],
        "calls_make": False,
        "installer_steps": [],
        "shared_uses": [],
    }
    if workflow["error"] is not None or not isinstance(workflow["parsed"], dict):
        return facts, False, []
    jobs = workflow["parsed"].get("jobs")
    runs = False
    pins: list[Pin] = []
    for job_id, job in jobs.items() if isinstance(jobs, dict) else []:
        for index, step in enumerate(_steps(job)):
            installers, shared, calls_make, step_runs, step_pins = _step_facts(
                f"{job_id}/{index}", step, rules
            )
            facts["installer_steps"] += installers
            facts["shared_uses"] += shared
            facts["calls_make"] = facts["calls_make"] or calls_make
            runs = runs or step_runs or bool(shared)
            pins += step_pins
    return facts, runs, pins


def _script_files(checkout: pathlib.Path) -> list[pathlib.Path]:
    """Return the regular files under the script directories, in stable order."""
    found: list[pathlib.Path] = []
    for name in SCRIPT_DIRECTORIES:
        base = checkout / name
        if not base.is_dir() or base.is_symlink():
            continue
        stack = [base]
        while stack:
            directory = stack.pop()
            for entry in sorted(directory.iterdir()):
                if entry.is_symlink():
                    continue
                if entry.is_dir():
                    if entry.name not in SKIPPED_DIRECTORIES:
                        stack.append(entry)
                elif entry.is_file():
                    found.append(entry)
    return sorted(found)


def _has_download_constant(source: str) -> bool:
    """Return whether Python *source* holds a prover release URL constant."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    return any(
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value.startswith(DOWNLOAD_PREFIXES)
        for node in ast.walk(tree)
    )


def _local_installer_reason(path: pathlib.Path, source: str | None) -> str | None:
    """Return why *path* is a local installer, or None when it is not."""
    stem = path.stem.lower()
    if any(word in stem for word in INSTALL_WORDS) and any(
        word in stem for word in TOOL_WORDS
    ):
        return "name"
    if source is None:
        return None
    if path.suffix in {".sh", ".bash"} and any(
        is_installer(argv) for argv in commands(source)
    ):
        return "command"
    if path.suffix == ".py" and _has_download_constant(source):
        return "download"
    return None


def _scripts(
    checkout: pathlib.Path,
) -> tuple[list[LocalInstaller], bool, list[str]]:
    """Return local installers, whether a script runs a prover, unreadable files."""
    installers: list[LocalInstaller] = []
    unreadable: list[str] = []
    runs = False
    for path in _script_files(checkout):
        relative = path.relative_to(checkout).as_posix()
        source: str | None = None
        if path.suffix in {".sh", ".bash", ".py"}:
            try:
                source = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as error:
                unreadable.append(f"{relative}: {error}")
        reason = _local_installer_reason(path, source)
        if reason is not None:
            installers.append({"path": relative, "reason": reason})
        if source is not None and path.suffix in {".sh", ".bash"}:
            runs = runs or any(is_runner(argv) for argv in commands(source))
    return installers, runs, unreadable


def _install_tests(checkout: pathlib.Path) -> list[str]:
    """Return test files whose names say they test prover installation."""
    base = checkout / TEST_DIRECTORY
    if not base.is_dir() or base.is_symlink():
        return []
    return sorted(
        path.relative_to(checkout).as_posix()
        for path in base.rglob("*")
        if path.is_file()
        and not path.is_symlink()
        and "install" in path.name.lower()
        and any(word in path.name.lower() for word in TEST_TOOL_WORDS)
    )


def _error(message: str, checkout: pathlib.Path) -> OperationalRuleError:
    """Create a declaration error with a stable operation boundary."""
    return OperationalRuleError(
        message, operation=OPERATION, resource=checkout / CONCORDAT_FILENAME
    )


def _text(entry: dict[str, object], key: str, checkout: pathlib.Path) -> str:
    """Return a declared string field, or fail naming the field."""
    value = entry.get(key)
    if not isinstance(value, str):
        message = f"a prover_tools exception needs a text {key}: {value!r}"
        raise _error(message, checkout)
    return value


def _record_present(checkout: pathlib.Path, record: str) -> bool:
    """Return whether *record* is a regular file inside the checkout."""
    posix = pathlib.PurePosixPath(record)
    if not record or posix.is_absolute() or ".." in posix.parts:
        return False
    candidate = checkout.joinpath(*posix.parts)
    if not candidate.resolve().is_relative_to(checkout.resolve()):
        return False
    return regular_file_exists(candidate, operation=OPERATION)


def _declared_exceptions(checkout: pathlib.Path) -> list[ProverException]:
    """Return the exceptions `.concordat` declares for prover tools."""
    rust = rust_language_mapping(checkout)
    block = rust.get("prover_tools") if rust is not None else None
    if block is None:
        return []
    if not isinstance(block, dict):
        message = "language.rust.prover_tools must be a mapping"
        raise _error(message, checkout)
    entries = typ.cast("dict[str, object]", block).get("exceptions")
    if entries is None:
        return []
    if not isinstance(entries, list):
        message = "language.rust.prover_tools.exceptions must be a list"
        raise _error(message, checkout)
    found: list[ProverException] = []
    for entry in typ.cast("list[object]", entries):
        if not isinstance(entry, dict):
            message = f"a prover_tools exception must be a mapping: {entry!r}"
            raise _error(message, checkout)
        mapping = typ.cast("dict[str, object]", entry)
        clauses = mapping.get("clauses")
        if not isinstance(clauses, list) or not all(
            isinstance(c, str) for c in clauses
        ):
            message = f"a prover_tools exception needs a list of clauses: {clauses!r}"
            raise _error(message, checkout)
        record = _text(mapping, "record", checkout)
        found.append({
            "clauses": typ.cast("list[str]", clauses),
            "pin": _text(mapping, "pin", checkout),
            "record": record,
            "rationale": _text(mapping, "rationale", checkout),
            "review_trigger": _text(mapping, "review_trigger", checkout),
            "record_present": _record_present(checkout, record),
        })
    return found


def _read_makefile(
    checkout: pathlib.Path, root: pathlib.Path, inspect: MakefileInspector
) -> tuple[MakefileState, MakeutilReport | None]:
    """Return the Makefile's state and report; a refusal is a fact, not a raise."""
    path = checkout / "Makefile"
    if not within_checkout(root, path, OPERATION) or not is_file(path, OPERATION):
        return {"state": "absent", "error": None}, None
    try:
        return {"state": "ok", "error": None}, inspect(path).report
    except MakefileRefusedError as error:
        return {"state": "refused", "error": str(error)}, None


def build_prover_tooling_envelope(
    checkout: pathlib.Path,
    *,
    inspect: MakefileInspector = inspect_makefile,
) -> ProverToolingEnvelope:
    """Assemble the prover tooling evidence for one checkout.

    Parameters
    ----------
    checkout:
        The repository checkout to describe.
    inspect:
        Reads the root Makefile's facts. The default is the pure query; the
        command boundary injects an observing one.

    An `OperationalRuleError` reaches the caller when the workflow directory,
    the Makefile or a declared record resolves outside the checkout, when the
    `.concordat` declaration is malformed, or when `makeutil` fails other than
    by refusing the file.

    Returns
    -------
    ProverToolingEnvelope
        The prover facts the policy judges.
    """
    root = resolved_root(checkout)
    state, report = _read_makefile(checkout, root, inspect)
    rules = _make_rules(report) if report is not None else MakeRules({}, {})
    pins = _ref_pins(checkout) + (_makefile_pins(report) if report is not None else [])
    runs = any(
        is_runner(argv) or is_installer(argv)
        for v in rules.recipes.values()
        for argv in v
    )
    workflows: list[WorkflowFacts] = []
    for workflow in _load_workflows(checkout):
        facts, workflow_runs, workflow_pins = _workflow_facts(workflow, rules)
        workflows.append(facts)
        runs = runs or workflow_runs
        pins += workflow_pins
    local, scripts_run, unreadable = _scripts(checkout)
    return {
        "schema_version": ENVELOPE_SCHEMA_VERSION,
        "kind": ENVELOPE_KIND,
        "repository": {"path": ".", "name": origin_slug(checkout)},
        "makefile": state,
        "version_files": [
            p
            for p in VERSION_FILES
            if regular_file_exists(checkout / p, operation=OPERATION)
        ],
        "pins": pins,
        "runs_prover": runs or scripts_run,
        "workflows": workflows,
        "local_installers": local,
        "install_tests": _install_tests(checkout),
        "unreadable": unreadable,
        "exceptions": _declared_exceptions(checkout),
    }
