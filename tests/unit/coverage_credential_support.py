"""Readers for the coverage publisher's upload guard and credential.

The trunk publisher learns whether the CodeScene credential is present from a
check step whose one command GitHub evaluates before any shell runs, so the
token enters no process and no step's `env`. The upload step is guarded on
that step's output and on the main ref, and passes the secret straight to the
action's `access-token` input. The token stays out of every `env` because the
upload action is composite: it hands the step's `env` to its nested
upload-artifact and cache steps, and binds the token itself from the input.

`test_coverage_topology_contract` applies these readers to this repository's
publisher, and `test_coverage_credential_readers` drives them against
synthetic documents.
"""

from __future__ import annotations

import re
import typing as typ

from tests.unit.coverage_topology_support import (
    MAIN_REF_GUARD,
    TOKEN_VARIABLE,
    UPLOAD_ACTION,
    Workflow,
    guard_conjuncts,
    job_steps,
    jobs,
    mentions,
)

if typ.TYPE_CHECKING:
    import collections.abc as cabc

# The check step's sole command. The expression is evaluated to `true` or
# `false` before the shell starts, so there is no shell conditional and the
# token reaches no process. A script reading the token from `env` would put
# the secret in the hands of checked-out branch code.
TOKEN_CHECK_COMMAND: typ.Final = (
    f'echo "available=${{{{ secrets.{TOKEN_VARIABLE} != \'\' }}}}" >> "$GITHUB_OUTPUT"'
)
# The one value the upload step's `access-token` input may hold.
TOKEN_SECRET: typ.Final = f"${{{{ secrets.{TOKEN_VARIABLE} }}}}"
_STEP_ID: typ.Final = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*")
_REF_GUARDS: typ.Final = frozenset({MAIN_REF_GUARD, "'refs/heads/main' == github.ref"})


class Upload(typ.NamedTuple):
    """One upload step and the token checks that precede it in its job.

    The label carries the step's position among the publisher's uploads as
    well as its name, because two steps may share a name or both omit one,
    and keying on the name alone lets a compliant step mask an offending one.
    """

    label: str
    step: dict[str, object]
    checks: frozenset[str]


def is_token_check(step: cabc.Mapping[str, object]) -> bool:
    """Return whether a step is a usable token check.

    It needs an identifier its output can be read under, the exact command,
    and no `if:`, since `false && X` contains X and a skipped check leaves
    the upload skipping forever.

    Returns
    -------
        Whether the step reports the token's presence and nothing else.
    """
    step_id = step.get("id")
    run = step.get("run")
    return (
        isinstance(step_id, str)
        and _STEP_ID.fullmatch(step_id) is not None
        and "if" not in step
        and "uses" not in step
        and isinstance(run, str)
        and run.strip() == TOKEN_CHECK_COMMAND
    )


def uploads(publisher: Workflow) -> list[Upload]:
    """Return the publisher's upload steps, each with its preceding checks.

    A check counts only in the upload's own job and only before it: a
    later step has not run when the condition is evaluated, and another
    job's step outputs are not in scope.

    Returns
    -------
        Every upload step in document order.
    """
    found: list[Upload] = []
    for name, job in jobs(publisher).items():
        checks: set[str] = set()
        for step in job_steps(publisher, name, job):
            if is_token_check(step):
                checks.add(str(step["id"]))
            elif isinstance(uses := step.get("uses"), str) and UPLOAD_ACTION in uses:
                label = f"upload {len(found)}: {step.get('name')}"
                found.append(Upload(label, step, frozenset(checks)))
    return found


def is_guarded_upload(condition: str, checks: cabc.Collection[str]) -> bool:
    """Return whether an upload condition requires the main ref and a check.

    Both must be whole conjuncts of a condition with no unquoted `||`. A
    substring test accepts `... && github.ref == 'refs/heads/main' ||
    github.event_name == 'workflow_dispatch'`, which uploads a dispatch from
    any branch. The credential conjunct must read the output of one of
    ``checks``; a guard on `env.CS_ACCESS_TOKEN` needs the token in `env`.

    Returns
    -------
        Whether both guards are whole conjuncts of an `&&`-only condition.

    Examples
    --------
    >>> is_guarded_upload(
    ...     "steps.t.outputs.available == 'true' && github.ref == 'refs/heads/main'",
    ...     {"t"},
    ... )
    True
    """
    conjuncts = guard_conjuncts(condition)
    if conjuncts is None:
        return False
    token_guards = {f"steps.{check}.outputs.available == 'true'" for check in checks}
    return bool(_REF_GUARDS.intersection(conjuncts)) and bool(
        token_guards.intersection(conjuncts)
    )


def unguarded_uploads(publisher: Workflow) -> dict[str, str]:
    """Return the publisher's upload steps whose condition is insufficient."""
    return {
        upload.label: condition
        for upload in uploads(publisher)
        if not is_guarded_upload(
            condition := str(upload.step.get("if", "")), upload.checks
        )
    }


def _expression_text(value: object) -> str:
    """Return a scalar with the spacing inside `${{ }}` normalized."""
    return re.sub(r"\$\{\{\s*(.*?)\s*\}\}", r"${{ \1 }}", str(value).strip())


def unpassed_credentials(publisher: Workflow) -> dict[str, str]:
    """Return the upload steps that do not pass the secret to the action.

    Returns
    -------
        Each offending step's label and its `access-token` input.
    """
    found: dict[str, str] = {}
    for upload in uploads(publisher):
        inputs = upload.step.get("with")
        value = inputs.get("access-token", "") if isinstance(inputs, dict) else ""
        if _expression_text(value) != TOKEN_SECRET:
            found[upload.label] = str(value)
    return found


def token_environments(publisher: Workflow) -> list[str]:
    """Return every `env` block in the publisher that names the token.

    Workflow, job and step scopes are all read: the composite upload action
    passes its step's `env` on to nested steps, and a job or workflow `env`
    reaches every step, the checked-out code's included.

    Returns
    -------
        The scopes whose `env` names the token, outermost first.
    """
    scopes: list[tuple[str, object]] = [("workflow", publisher.document.get("env"))]
    for name, job in jobs(publisher).items():
        scopes.append((f"job {name!r}", job.get("env")))
        scopes.extend(
            (f"job {name!r} step {index}", step.get("env"))
            for index, step in enumerate(job_steps(publisher, name, job))
        )
    return [scope for scope, env in scopes if mentions(env, TOKEN_VARIABLE)]
