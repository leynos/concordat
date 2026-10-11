"""Read which toolchains a repository's workflows name, as data.

RT-026 and RT-027 compare the toolchain CI uses with the pin and the declared
MSRV, so the question is "which toolchains do the steps select", not "which
version strings appear in the files". Workflows are therefore walked as decoded
YAML: a toolchain in a comment or in a step's display name is not CI using it,
and a `${{ env.NAME }}` reference is followed to the literal it names.

A workflow that cannot be decoded stays a fact with its reason, so the policy
reports the clauses that depend on it as indeterminate.
"""

from __future__ import annotations

import dataclasses
import re
import typing as typ

from .markdown_envelope import load_workflows, resolved_root

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    import pathlib

_NIGHTLY: typ.Final = re.compile(r"^nightly-\d{4}-\d{2}-\d{2}$")
_NUMERIC: typ.Final = re.compile(r"^(?P<minor>\d+\.\d+)(?:\.\d+)?$")
_REFERENCE: typ.Final = re.compile(
    r"^\$\{\{\s*(?P<scope>env|matrix)\.(?P<name>[\w-]+)\s*\}\}$"
)
_EXPRESSION: typ.Final = re.compile(r"\$\{\{\s*(?P<body>[^}]*?)\s*\}\}")
_COMMIT_SHA: typ.Final = re.compile(r"[0-9a-f]{40}")
# The places a step names a toolchain in a command: rustup installing,
# selecting or running one, and cargo's `+toolchain` override. `install` takes
# every positional name; the other verbs take the first.
_RUSTUP: typ.Final = re.compile(
    r"rustup\s+(?P<verb>toolchain\s+install|install|default|override\s+set|run)"
    r"(?P<args>(?:\s+[^\s;&|]+)+)"
)
_CARGO_OVERRIDE: typ.Final = re.compile(r"\bcargo\s+\+(?P<value>[^\s;&|]+)")
# rustup options that take a value, so the value is not mistaken for a toolchain.
_VALUE_OPTIONS: typ.Final = frozenset({
    "--profile",
    "-c",
    "--component",
    "-t",
    "--target",
})
_MSRV_JOB: typ.Final = re.compile(r"(?i)\bmsrv\b|cargo[- ]msrv")
_READS_MANIFEST_MSRV: typ.Final = re.compile(r"(?i)rust[-_]version")
_SELECTS_TOOLCHAIN: typ.Final = re.compile(r"(?i)rustup|toolchain")
_TOOLCHAIN_ACTIONS: typ.Final = (
    "dtolnay/rust-toolchain",
    "actions-rust-lang/setup-rust-toolchain",
    "leynos/shared-actions/.github/actions/setup-rust",
)


class CiFacts(typ.TypedDict):
    """Store which toolchains the workflows name, read as data.

    Attributes
    ----------
    errors:
        Workflow paths that did not decode, each with its reason. The policy
        cannot judge CI from a workflow it could not read.
    dated_nightlies:
        Every distinct dated-nightly toolchain a workflow names.
    numeric_minors:
        Every distinct `major.minor` of a numeric release a workflow names.
    msrv_job:
        True when a job id or name, or a step command, is about the MSRV.
    reads_manifest_msrv:
        True when a toolchain-selecting step reads the manifest's
        `rust-version`, so every declared MSRV is exercised by construction.
    """

    errors: list[str]
    dated_nightlies: list[str]
    numeric_minors: list[str]
    msrv_job: bool
    reads_manifest_msrv: bool


@dataclasses.dataclass(slots=True)
class _Evidence:
    """Accumulate the evidence found across every workflow."""

    names: set[str] = dataclasses.field(default_factory=set)
    msrv_job: bool = False
    reads_manifest_msrv: bool = False


def minor_of(version: str) -> str | None:
    """Return the `major.minor` of a numeric version, or None."""
    match = _NUMERIC.fullmatch(version)
    return match.group("minor") if match else None


def _collect_env(node: object, env: dict[str, str]) -> None:
    """Gather every literal `env:` entry in a workflow into *env*."""
    match node:
        case dict():
            for key, value in node.items():
                if key == "env" and isinstance(value, dict):
                    env.update({
                        str(name): text
                        for name, text in value.items()
                        if isinstance(text, str)
                    })
                _collect_env(value, env)
        case list():
            for item in node:
                _collect_env(item, env)
        case _:
            pass


def _stringify(value: object) -> str | None:
    """Return a matrix scalar as the text rustup would see, or None."""
    if isinstance(value, bool) or not isinstance(value, str | int | float):
        return None
    return str(value)


def _axis_entries(key: object, listed: object) -> list[dict[object, object]]:
    """Return a matrix axis as one mapping per leg it contributes."""
    if key == "include":
        return [
            typ.cast("dict[object, object]", entry)
            for entry in _as_list(listed)
            if isinstance(entry, dict)
        ]
    return [{key: item} for item in _as_list(listed)]


def _matrix_entries(matrix: object) -> cabc.Iterator[dict[object, object]]:
    """Yield every leg-sized mapping the matrix's axes and `include` contribute."""
    if isinstance(matrix, dict):
        for key, listed in matrix.items():
            yield from _axis_entries(key, listed)


def _scalar_items(entry: dict[object, object]) -> cabc.Iterator[tuple[str, str]]:
    """Yield `(name, text)` for each scalar value in one matrix leg."""
    for name, item in entry.items():
        text = _stringify(item)
        if text is not None:
            yield str(name), text


def _matrix_values(matrix: object) -> dict[str, list[str]]:
    """Map each matrix key to every scalar it takes, `include` entries too."""
    values: dict[str, list[str]] = {}
    for entry in _matrix_entries(matrix):
        for name, text in _scalar_items(entry):
            values.setdefault(name, []).append(text)
    return values


def _as_list(value: object) -> list[object]:
    """Return *value* as a list; a matrix axis that is not a list has no values."""
    return typ.cast("list[object]", value) if isinstance(value, list) else []


def _resolve(value: object, scope: _Scope) -> list[str]:
    """Resolve a toolchain value through one `env` or `matrix` reference."""
    if not isinstance(value, str):
        return []
    text = value.strip()
    reference = _REFERENCE.match(text)
    if reference is None:
        return [text] if text else []
    name = reference.group("name")
    if reference.group("scope") == "matrix":
        return scope.matrix.get(name, [])
    return [scope.env[name]] if scope.env.get(name) else []


def _action_toolchains(step: dict[str, object], scope: _Scope) -> list[str]:
    """Return the toolchains a toolchain-installing action step selects."""
    uses = step.get("uses")
    if not isinstance(uses, str) or not uses.startswith(_TOOLCHAIN_ACTIONS):
        return []
    inputs = step.get("with")
    names = _resolve(inputs.get("toolchain"), scope) if isinstance(inputs, dict) else []
    # `dtolnay/rust-toolchain@nightly-2026-03-26` names its toolchain in the
    # ref, which is the action's documented way to select one.
    ref = uses.rpartition("@")[2]
    if ref and not _COMMIT_SHA.fullmatch(ref):
        names.append(ref)
    return names


def _positional_names(args: str) -> list[str]:
    """Return the non-option words of a `rustup` argument string."""
    names: list[str] = []
    words = iter(args.split())
    for word in words:
        if word in _VALUE_OPTIONS:
            next(words, None)
        elif not word.startswith("-"):
            names.append(word.strip("\"'"))
    return names


def _rustup_toolchains(run: str) -> list[str]:
    """Return the toolchains `rustup` commands in *run* install or select."""
    names: list[str] = []
    for match in _RUSTUP.finditer(run):
        positional = _positional_names(match.group("args"))
        takes_all = "install" in match.group("verb")
        names += positional if takes_all else positional[:1]
    return names


def _command_toolchains(step: dict[str, object], scope: _Scope) -> list[str]:
    """Return the toolchains a `run` command installs, selects or overrides."""
    run = step.get("run")
    if not isinstance(run, str):
        return []
    # Collapse `${{ matrix.x }}` so an expression is one word, not three.
    tight = _EXPRESSION.sub(
        lambda m: "${{" + m.group("body").replace(" ", "") + "}}", run
    )
    found = _rustup_toolchains(tight) + [
        m.group("value") for m in _CARGO_OVERRIDE.finditer(tight)
    ]
    return [name for token in found for name in _resolve(token, scope)]


@dataclasses.dataclass(frozen=True, slots=True)
class _Scope:
    """The values a `${{ env.X }}` or `${{ matrix.X }}` reference can take."""

    env: dict[str, str]
    matrix: dict[str, list[str]]


def _job_steps(
    job_id: object, job: object
) -> cabc.Iterator[tuple[str, dict[str, list[str]], dict[str, object]]]:
    """Yield `(job label, matrix values, step)` for every mapping step of a job."""
    if not isinstance(job, dict):
        return
    steps = job.get("steps")
    if not isinstance(steps, list):
        return
    strategy = job.get("strategy")
    matrix = _matrix_values(
        strategy.get("matrix") if isinstance(strategy, dict) else None
    )
    label = f"{job_id} {job.get('name', '')}"
    yield from (
        (label, matrix, typ.cast("dict[str, object]", step))
        for step in steps
        if isinstance(step, dict)
    )


def _walk_steps(
    document: object,
) -> cabc.Iterator[tuple[str, dict[str, list[str]], dict[str, object]]]:
    """Yield `(job label, matrix values, step)` for every step in a workflow."""
    jobs = document.get("jobs") if isinstance(document, dict) else None
    if isinstance(jobs, dict):
        for job_id, job in jobs.items():
            yield from _job_steps(job_id, job)


def _step_text(label: str, step: dict[str, object]) -> str:
    """Return the words a step is about: its job, its name and its command."""
    run = step.get("run")
    return f"{label} {step.get('name', '')} {run if isinstance(run, str) else ''}"


def _read_workflow(document: object, evidence: _Evidence) -> None:
    """Add one decoded workflow's toolchain evidence to *evidence*."""
    env: dict[str, str] = {}
    _collect_env(document, env)
    for label, matrix, step in _walk_steps(document):
        scope = _Scope(env, matrix)
        evidence.names.update(_action_toolchains(step, scope))
        evidence.names.update(_command_toolchains(step, scope))
        text = _step_text(label, step)
        evidence.msrv_job |= bool(_MSRV_JOB.search(text))
        evidence.reads_manifest_msrv |= bool(
            _SELECTS_TOOLCHAIN.search(text) and _READS_MANIFEST_MSRV.search(text)
        )


def ci_facts(checkout: pathlib.Path) -> CiFacts:
    """Read the workflows' toolchain evidence, retaining decode failures.

    Returns
    -------
    CiFacts
        The toolchains the workflows select and any workflow that did not
        decode.
    """
    errors: list[str] = []
    evidence = _Evidence()
    for workflow in load_workflows(checkout, resolved_root(checkout)):
        if workflow["error"] is None:
            _read_workflow(workflow["parsed"], evidence)
        else:
            errors.append(f"{workflow['path']}: {workflow['error']}")
    return {
        "errors": errors,
        "dated_nightlies": sorted(n for n in evidence.names if _NIGHTLY.fullmatch(n)),
        "numeric_minors": sorted({
            m for n in evidence.names if (m := minor_of(n)) is not None
        }),
        "msrv_job": evidence.msrv_job,
        "reads_manifest_msrv": evidence.reads_manifest_msrv,
    }
