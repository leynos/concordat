"""Build the facts for the `nested-cargo-ci-wiring` rule (NC-006).

A nested `cargo` run is a child process, so it inherits whatever the CI job
exports. Two wiring mistakes leave it cold, and the envelope records the facts
for both:

- a workflow that points `CARGO_TARGET_DIR` at a per-run or temporary path
  (the runner's temp directory, the run id or number, the commit SHA, a
  `mktemp` directory, `/tmp`), so every run, and every nested build in it,
  starts with an empty target tree and sccache keys miss because the paths
  differ;
- a job that opts out of the compiler-cache wrapper with `setup-rust`'s
  `export-rustc-wrapper: false` and then names `RUSTC_WRAPPER` nowhere, so its
  child `cargo` compiles without sccache. `setup-rust` exports the wrapper
  through `GITHUB_ENV` by default, which reaches every later step and so every
  child `cargo`, without any job-level `env`; the rule therefore does not ask
  for one. The action documents the opt-out for nested builds such as trybuild
  fixtures, where each compile is a miss that is also stored; a job that opts
  out but scopes the wrapper itself (`RUSTC_WRAPPER="$SCCACHE_PATH" cargo ...`)
  names it and passes.

The builder reads each workflow's decoded document and records, per workflow,
every place `CARGO_TARGET_DIR` is set (workflow, job and step `env`, and
`CARGO_TARGET_DIR=` assignments in `run` scripts) with its value, and per job
whether `setup-rust` opts out and whether `RUSTC_WRAPPER` appears anywhere in
the workflow or job. The Rego policy classifies the values, so the temporary
patterns are rule parameters. A workflow that cannot be decoded stays a fact
with its reason, so the policy answers indeterminate.
"""

from __future__ import annotations

import re
import typing as typ

from .codescene_coverage_envelope import _load_workflows
from .nested_cargo import origin_slug

if typ.TYPE_CHECKING:
    import pathlib

    from .codescene_coverage_envelope import Repository, WorkflowFile

ENVELOPE_SCHEMA_VERSION: typ.Final = 1
ENVELOPE_KIND: typ.Final = "policy-input/nested-cargo-ci-wiring"
TARGET_VARIABLE: typ.Final = "CARGO_TARGET_DIR"
WRAPPER_VARIABLE: typ.Final = "RUSTC_WRAPPER"
SETUP_ACTION: typ.Final = "setup-rust"
OPT_OUT_INPUT: typ.Final = "export-rustc-wrapper"
FALSE_SPELLINGS: typ.Final = frozenset({"false", "0", "no", "off"})
RUN_ASSIGNMENT: typ.Final = re.compile(
    rf"{TARGET_VARIABLE}=(?P<value>\"[^\"\n]*\"|'[^'\n]*'|[^\s;&|]+)"
)


class TargetSetting(typ.TypedDict):
    """Store one place a workflow sets `CARGO_TARGET_DIR`.

    Attributes
    ----------
    where:
        `env` (workflow), `job <id>` or `step <job>/<index>`, with `run` appended
        for an assignment inside a `run` script.
    value:
        The value as written, expressions unevaluated.
    """

    where: str
    value: str


class JobWiring(typ.TypedDict):
    """Store a job's compiler-cache wrapper wiring.

    Attributes
    ----------
    id:
        The job id.
    opts_out:
        Whether a `setup-rust` step sets `export-rustc-wrapper` to false.
    wrapper_named:
        Whether `RUSTC_WRAPPER` appears in the workflow's or the job's `env`, in
        a step's `env`, or in a `run` script of the job.
    """

    id: str
    opts_out: bool
    wrapper_named: bool


class WorkflowWiring(typ.TypedDict):
    """Store one workflow's wiring facts, or the reason it could not be read.

    Attributes
    ----------
    path:
        The workflow's path relative to the checkout, in POSIX form.
    error:
        Why the workflow could not be decoded, or None.
    target_settings:
        Every place the workflow sets `CARGO_TARGET_DIR`.
    jobs:
        Each job's wrapper wiring.
    """

    path: str
    error: str | None
    target_settings: list[TargetSetting]
    jobs: list[JobWiring]


class NestedCargoWiringEnvelope(typ.TypedDict):
    """Store the evidence evaluated by the nested-cargo CI wiring rule.

    Attributes
    ----------
    schema_version:
        The envelope's schema version, which the policy checks.
    kind:
        The policy-input identifier, which the policy checks.
    repository:
        The checkout's path and, where the origin names one, its GitHub slug.
    workflows:
        Every root workflow, with its wiring facts or its decoding error.
    """

    schema_version: int
    kind: str
    repository: Repository
    workflows: list[WorkflowWiring]


def _mapping(value: object) -> dict[str, object]:
    """Return *value* as a mapping, or an empty one."""
    return typ.cast("dict[str, object]", value) if isinstance(value, dict) else {}


def _steps(job: dict[str, object]) -> list[dict[str, object]]:
    """Return a job's step mappings."""
    steps = job.get("steps")
    return [_mapping(step) for step in steps] if isinstance(steps, list) else []


def _env_target(env: object, where: str) -> list[TargetSetting]:
    """Return the `CARGO_TARGET_DIR` entry of an `env` mapping, if any."""
    table = _mapping(env)
    if TARGET_VARIABLE not in table:
        return []
    return [{"where": where, "value": str(table[TARGET_VARIABLE])}]


def _run_targets(run: object, where: str) -> list[TargetSetting]:
    """Return every `CARGO_TARGET_DIR=value` assignment in a `run` script."""
    if not isinstance(run, str):
        return []
    return [
        {"where": f"{where} run", "value": match["value"].strip("\"'")}
        for match in RUN_ASSIGNMENT.finditer(run)
    ]


def _job_targets(job_id: str, job: dict[str, object]) -> list[TargetSetting]:
    """Return the `CARGO_TARGET_DIR` settings of one job and its steps."""
    found = _env_target(job.get("env"), f"job {job_id}")
    for index, step in enumerate(_steps(job)):
        where = f"step {job_id}/{index}"
        found += _env_target(step.get("env"), where)
        found += _run_targets(step.get("run"), where)
    return found


def _opts_out(step: dict[str, object]) -> bool:
    """Report whether a step is `setup-rust` with the wrapper export disabled."""
    uses = step.get("uses")
    if not isinstance(uses, str):
        return False
    action = uses.split("@", maxsplit=1)[0].rsplit("/", maxsplit=1)[-1]
    if action != SETUP_ACTION:
        return False
    given = _mapping(step.get("with")).get(OPT_OUT_INPUT)
    return given is False or str(given).strip().lower() in FALSE_SPELLINGS


def _names_wrapper(job: dict[str, object], workflow_env: object) -> bool:
    """Report whether `RUSTC_WRAPPER` is named in the workflow, job or its steps."""
    if WRAPPER_VARIABLE in _mapping(workflow_env) or WRAPPER_VARIABLE in _mapping(
        job.get("env")
    ):
        return True
    return any(
        WRAPPER_VARIABLE in _mapping(step.get("env"))
        or WRAPPER_VARIABLE in str(step.get("run", ""))
        for step in _steps(job)
    )


def _job_wiring(job_id: str, job: dict[str, object], workflow_env: object) -> JobWiring:
    """Return one job's wrapper wiring."""
    return {
        "id": job_id,
        "opts_out": any(_opts_out(step) for step in _steps(job)),
        "wrapper_named": _names_wrapper(job, workflow_env),
    }


def _wiring(workflow: WorkflowFile) -> WorkflowWiring:
    """Return the wiring facts for one decoded workflow."""
    base: WorkflowWiring = {
        "path": workflow["path"],
        "error": workflow["error"],
        "target_settings": [],
        "jobs": [],
    }
    if workflow["error"] is not None:
        return base
    document = _mapping(workflow["parsed"])
    jobs = {
        str(name): _mapping(job) for name, job in _mapping(document.get("jobs")).items()
    }
    settings = _env_target(document.get("env"), "env")
    for job_id, job in jobs.items():
        settings += _job_targets(job_id, job)
    return {
        **base,
        "target_settings": settings,
        "jobs": [
            _job_wiring(job_id, job, document.get("env"))
            for job_id, job in jobs.items()
        ],
    }


def build_nested_cargo_wiring_envelope(
    checkout: pathlib.Path,
) -> NestedCargoWiringEnvelope:
    """Assemble the CI wiring evidence for the nested-cargo rule.

    Parameters
    ----------
    checkout:
        The repository checkout to describe.

    An `OperationalRuleError` reaches the caller when the workflow directory
    resolves outside the checkout or cannot be listed.

    Returns
    -------
    NestedCargoWiringEnvelope
        Each workflow's `CARGO_TARGET_DIR` settings and per-job wrapper wiring.
    """
    return {
        "schema_version": ENVELOPE_SCHEMA_VERSION,
        "kind": ENVELOPE_KIND,
        "repository": {"path": ".", "name": origin_slug(checkout)},
        "workflows": [_wiring(workflow) for workflow in _load_workflows(checkout)],
    }
