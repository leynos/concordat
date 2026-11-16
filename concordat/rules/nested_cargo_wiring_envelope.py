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
# Actions that only prepare the machine; none runs a test or a build.
SETUP_ACTIONS: typ.Final = frozenset({
    "checkout",
    "setup-rust",
    "rust-toolchain",
    "install-action",
    "cache",
    "upload-artifact",
    "download-artifact",
})
CHECK_SUBCOMMANDS: typ.Final = frozenset({"check", "clippy", "fmt", "metadata", "tree"})
# `cargo [+toolchain] <subcommand>`; the toolchain selector may be quoted.
CARGO_COMMAND: typ.Final = re.compile(
    r"(?<![\w./-])cargo[ \t]+(?:[\"']?\+[^\s\"']+[\"']?[ \t]+)?"
    r"(?P<subcommand>[a-z][\w-]*)"
)
# A script that runs these may start a test or another cargo behind the reader.
HANDS_OFF: typ.Final = re.compile(
    r"(?<![\w-])(?:make|just|nextest|pytest|npm|pnpm|bun|uv|python3?|bash|sh)(?![\w-])"
    r"|\./|(?<![\w.])cargo-[a-z]"
)
# An unquoted value is a run of ordinary characters and whole `${{ .. }}`
# expressions, because GitHub substitutes an expression (which may hold spaces)
# before the shell runs; the variable name needs a boundary so that
# `OTHER_CARGO_TARGET_DIR=` is not read as this variable.
RUN_ASSIGNMENT: typ.Final = re.compile(
    rf"(?<!\w){TARGET_VARIABLE}="
    r"(?P<value>\"[^\"\n]*\"|'[^'\n]*'|(?:\$\{\{.*?\}\}|[^\s;&|])+)"
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
    job:
        The id of the job the setting belongs to, or None for a workflow-level
        `env`, which every job of the workflow inherits.
    """

    where: str
    value: str
    job: str | None


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
    check_only:
        Whether the job can start no nested build: it runs cargo, every cargo
        command is a check-style one (`check`, `clippy`, `fmt`, `metadata`,
        `tree`), no `run` script hands off to `make`, a test runner, an
        interpreter or a script, and every action it uses is a plain
        set-up one. A per-run target costs such a job nothing, because there
        is no second build to inherit a warm tree.
    """

    id: str
    opts_out: bool
    wrapper_named: bool
    check_only: bool


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


def _env_target(env: object, where: str, job: str | None) -> list[TargetSetting]:
    """Return the `CARGO_TARGET_DIR` entry of an `env` mapping, if any."""
    table = _mapping(env)
    if TARGET_VARIABLE not in table:
        return []
    return [{"where": where, "value": str(table[TARGET_VARIABLE]), "job": job}]


def _run_targets(run: object, where: str, job: str) -> list[TargetSetting]:
    """Return every `CARGO_TARGET_DIR=value` assignment in a `run` script."""
    if not isinstance(run, str):
        return []
    # A comment line is documentation, not an assignment.
    script = "\n".join(
        line for line in run.splitlines() if not line.lstrip().startswith("#")
    )
    return [
        {"where": f"{where} run", "value": match["value"].strip("\"'"), "job": job}
        for match in RUN_ASSIGNMENT.finditer(script)
    ]


def _job_targets(job_id: str, job: dict[str, object]) -> list[TargetSetting]:
    """Return the `CARGO_TARGET_DIR` settings of one job and its steps."""
    found = _env_target(job.get("env"), f"job {job_id}", job_id)
    for index, step in enumerate(_steps(job)):
        where = f"step {job_id}/{index}"
        found += _env_target(step.get("env"), where, job_id)
        found += _run_targets(step.get("run"), where, job_id)
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


def _run_scripts(job: dict[str, object]) -> list[str]:
    """Return a job's `run` scripts, comment lines removed."""
    return [
        "\n".join(
            line
            for line in str(step["run"]).splitlines()
            if not line.lstrip().startswith("#")
        )
        for step in _steps(job)
        if isinstance(step.get("run"), str)
    ]


def _uses_only_setup_actions(job: dict[str, object]) -> bool:
    """Report whether every `uses` step of a job is a plain set-up action."""
    for step in _steps(job):
        uses = step.get("uses")
        if uses is None:
            continue
        action = str(uses).split("@", maxsplit=1)[0].rsplit("/", maxsplit=1)[-1]
        if action not in SETUP_ACTIONS:
            return False
    return True


def _is_check_only(job: dict[str, object]) -> bool:
    """Report whether a job can start no nested cargo build.

    The test is deliberately narrow: a job qualifies only when it runs cargo,
    every cargo command it runs is check-style, and nothing else in its scripts
    or actions could run tests or hand the work to another program. Anything
    the reader cannot place leaves the job subject to the rule.

    Returns
    -------
    bool
        True when the job is a bare check job with no nested build to protect.
    """
    scripts = _run_scripts(job)
    subcommands = [
        found["subcommand"]
        for script in scripts
        for found in CARGO_COMMAND.finditer(script)
    ]
    return (
        bool(subcommands)
        and all(name in CHECK_SUBCOMMANDS for name in subcommands)
        and not any(HANDS_OFF.search(script) for script in scripts)
        and _uses_only_setup_actions(job)
    )


def _job_wiring(job_id: str, job: dict[str, object], workflow_env: object) -> JobWiring:
    """Return one job's wrapper wiring."""
    return {
        "id": job_id,
        "opts_out": any(_opts_out(step) for step in _steps(job)),
        "wrapper_named": _names_wrapper(job, workflow_env),
        "check_only": _is_check_only(job),
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
    settings = _env_target(document.get("env"), "env", None)
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
