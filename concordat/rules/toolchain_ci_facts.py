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
_ENV_REFERENCE: typ.Final = re.compile(r"^\$\{\{\s*env\.(?P<name>\w+)\s*\}\}$")
_COMMIT_SHA: typ.Final = re.compile(r"[0-9a-f]{40}")
# The two places a step names a toolchain in a command: rustup installing or
# selecting one, and cargo's `+toolchain` override.
_COMMAND_TOOLCHAINS: typ.Final = (
    re.compile(
        r"rustup\s+(?:toolchain\s+install|default|override\s+set)\s+(?P<value>[\w.-]+)"
    ),
    re.compile(r"\bcargo\s+\+(?P<value>[\w.-]+)"),
)
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
    match = _NUMERIC.match(version)
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


def _resolve(value: object, env: dict[str, str]) -> list[str]:
    """Resolve a toolchain value, following one `${{ env.NAME }}` reference."""
    if not isinstance(value, str):
        return []
    reference = _ENV_REFERENCE.match(value.strip())
    resolved = env.get(reference.group("name"), "") if reference else value.strip()
    return [resolved] if resolved else []


def _action_toolchains(step: dict[str, object], env: dict[str, str]) -> list[str]:
    """Return the toolchains a toolchain-installing action step selects."""
    uses = step.get("uses")
    if not isinstance(uses, str) or not uses.startswith(_TOOLCHAIN_ACTIONS):
        return []
    inputs = step.get("with")
    names = _resolve(inputs.get("toolchain"), env) if isinstance(inputs, dict) else []
    # `dtolnay/rust-toolchain@nightly-2026-03-26` names its toolchain in the
    # ref, which is the action's documented way to select one.
    ref = uses.rpartition("@")[2]
    if ref and not _COMMIT_SHA.fullmatch(ref):
        names.append(ref)
    return names


def _command_toolchains(step: dict[str, object]) -> list[str]:
    """Return the toolchains a `run` command installs, selects or overrides."""
    run = step.get("run")
    if not isinstance(run, str):
        return []
    return [
        match.group("value")
        for pattern in _COMMAND_TOOLCHAINS
        for match in pattern.finditer(run)
    ]


def _job_steps(
    job_id: object, job: object
) -> cabc.Iterator[tuple[str, dict[str, object]]]:
    """Yield `(job label, step)` for every mapping step of one job."""
    if not isinstance(job, dict):
        return
    steps = job.get("steps")
    if not isinstance(steps, list):
        return
    label = f"{job_id} {job.get('name', '')}"
    yield from (
        (label, typ.cast("dict[str, object]", step))
        for step in steps
        if isinstance(step, dict)
    )


def _walk_steps(document: object) -> cabc.Iterator[tuple[str, dict[str, object]]]:
    """Yield `(job label, step)` for every step of every job in a workflow."""
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
    for label, step in _walk_steps(document):
        evidence.names.update(_action_toolchains(step, env))
        evidence.names.update(_command_toolchains(step))
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
        "dated_nightlies": sorted(n for n in evidence.names if _NIGHTLY.match(n)),
        "numeric_minors": sorted({
            m for n in evidence.names if (m := minor_of(n)) is not None
        }),
        "msrv_job": evidence.msrv_job,
        "reads_manifest_msrv": evidence.reads_manifest_msrv,
    }
