"""Readers for the coverage topology contract.

`test_coverage_topology_contract` judges this repository's workflows and
`test_coverage_topology_readers` drives these readers against synthetic
documents, so both see the topology through the same eyes. Nothing here
asserts a repository fact; shape errors raise, so a document these readers
cannot interpret fails loudly rather than reading as compliant.
"""

from __future__ import annotations

import itertools
import re
import typing as typ
from pathlib import Path, PurePosixPath

from ruamel.yaml import YAML

if typ.TYPE_CHECKING:
    import collections.abc as cabc

REPOSITORY_ROOT: typ.Final = Path(__file__).parents[2]
_LOCAL_WORKFLOW_PREFIX: typ.Final = PurePosixPath(".github/workflows")

COVERAGE_ACTION: typ.Final = "generate-coverage@"
UPLOAD_ACTION: typ.Final = "upload-codescene-coverage@"
MAIN_REF_GUARD: typ.Final = "github.ref == 'refs/heads/main'"
# The credential's name, not a credential: the contract asserts where the
# name may and may not appear.
TOKEN_VARIABLE: typ.Final = "CS_ACCESS_TOKEN"  # noqa: S105 - a variable name
CODESCENE_HOST: typ.Final = "codescene.io"

# The two spellings a trigger mapping arrives under. YAML 1.1 reads an
# unquoted `on:` as the boolean true, so a reader that looks only for the
# string key finds nothing and every clause filtering by trigger passes
# vacuously.
_TRIGGER_KEYS: typ.Final = ("on", True)


class Workflow(typ.NamedTuple):
    """One parsed workflow document and its repository-relative path.

    The document is keyed on `object` rather than `str`, because YAML 1.1
    reads an unquoted `on:` as the boolean true, so a workflow's own keys are
    not all strings.
    """

    path: str
    document: dict[object, object]

    def __str__(self) -> str:
        """Return the workflow's repository-relative path."""
        return self.path


def _mapping(value: object, *, subject: str) -> dict[str, object]:
    """Return a string-keyed mapping, naming the unexpected ``subject``."""
    if not isinstance(value, dict):
        msg = f"expected {subject} to be a mapping"
        raise TypeError(msg)
    return typ.cast("dict[str, object]", value)


def parse_workflow(path: str, text: str) -> Workflow:
    """Parse one workflow document.

    ruamel's safe loader refuses a duplicate mapping key rather than keeping
    the last one silently, so a document declaring a key twice fails here.

    Returns
    -------
        The parsed document with its path.

    Raises
    ------
    TypeError
        When the document is not a mapping.
    """
    document = YAML(typ="safe").load(text)
    if not isinstance(document, dict):
        msg = f"expected the {path} workflow to be a mapping"
        raise TypeError(msg)
    return Workflow(path, typ.cast("dict[object, object]", document))


def workflows(root: Path = REPOSITORY_ROOT) -> tuple[Workflow, ...]:
    """Return every parsed workflow under ``root``, sorted by path."""
    directory = root / _LOCAL_WORKFLOW_PREFIX
    paths = sorted(
        path
        for path in directory.iterdir()
        if path.is_file() and path.suffix.lower() in {".yml", ".yaml"}
    )
    return tuple(
        parse_workflow(
            path.relative_to(root).as_posix(), path.read_text(encoding="utf-8")
        )
        for path in paths
    )


def triggers(document: cabc.Mapping[object, object]) -> dict[str, object]:
    """Return a workflow's trigger mapping, under either spelling of the key.

    `on: push` and `on: [push, pull_request]` carry no filters, so each
    name maps to `None`. A form this reader does not understand raises
    rather than reading as a workflow with no triggers.

    A workflow declaring both spellings is refused: GitHub merges them, and
    a reader that picks one is blind to the other.

    Returns
    -------
        The trigger mapping, empty for a workflow with no triggers.

    Raises
    ------
    TypeError
        When the workflow declares its triggers under both spellings.
    """
    present = [key for key in _TRIGGER_KEYS if key in document]
    if len(present) > 1:
        msg = "a workflow declares its triggers under both `on` and `true`"
        raise TypeError(msg)
    return _trigger_mapping(document[present[0]]) if present else {}


def _trigger_mapping(value: object) -> dict[str, object]:
    """Return one trigger value as a mapping, refusing an unknown form.

    Returns
    -------
        The trigger mapping, one `None`-valued entry per bare name.

    Raises
    ------
    TypeError
        When the value is neither a name, a list of names, nor a mapping.
    """
    if isinstance(value, dict):
        return typ.cast("dict[str, object]", value)
    names = [value] if isinstance(value, str) else value
    if isinstance(names, list) and all(isinstance(name, str) for name in names):
        return {typ.cast("str", name): None for name in names}
    msg = f"unrecognized trigger form: {value!r}"
    raise TypeError(msg)


def serves_pull_requests(document: cabc.Mapping[object, object]) -> bool:
    """Return whether a workflow is triggered by a pull-request event."""
    return any(name.startswith("pull_request") for name in triggers(document))


def pushes_to_main(document: cabc.Mapping[object, object]) -> bool:
    """Return whether a workflow runs on a push restricted to `main` alone.

    A filter naming `main` among other branches would publish those
    branches' coverage as the trunk's, so only `main` by itself counts.

    Returns
    -------
        Whether the push filter is exactly `main`.
    """
    push = triggers(document).get("push")
    if not isinstance(push, dict):
        return False
    return push.get("branches") in ("main", ["main"])


def jobs(workflow: Workflow) -> dict[str, dict[str, object]]:
    """Return the workflow's jobs, keyed by job identifier."""
    declared = workflow.document.get("jobs")
    if declared is None:
        return {}
    return {
        name: _mapping(job, subject=f"{workflow} job {name!r}")
        for name, job in _mapping(declared, subject=f"{workflow} jobs").items()
    }


def steps(workflow: Workflow) -> list[dict[str, object]]:
    """Return every step of every job in a workflow, in document order."""
    collected: list[dict[str, object]] = []
    for name, job in jobs(workflow).items():
        declared = job.get("steps")
        if isinstance(declared, list):
            collected.extend(
                _mapping(step, subject=f"{workflow} job {name!r} step")
                for step in declared
            )
    return collected


def steps_using(workflow: Workflow, action: str) -> list[dict[str, object]]:
    """Return the workflow's steps that invoke ``action``."""
    return [
        step
        for step in steps(workflow)
        if isinstance(uses := step.get("uses"), str) and action in uses
    ]


def coverage_steps(workflow: Workflow) -> list[dict[str, object]]:
    """Return the workflow's `generate-coverage` steps."""
    return steps_using(workflow, COVERAGE_ACTION)


def publishes_report(step: cabc.Mapping[str, object]) -> bool:
    """Return whether a coverage step publishes its report as an artefact.

    The action defaults the input to `"true"`, so a step that omits it
    publishes. The effective value is what this reads, not the spelling.

    Returns
    -------
        Whether the step's effective `publish-artefact` value is truthy.
    """
    inputs = step.get("with")
    declared = (
        inputs.get("publish-artefact", True) if isinstance(inputs, dict) else True
    )
    if isinstance(declared, bool):
        return declared
    return str(declared).strip().lower() != "false"


def local_callee(uses: str) -> str | None:
    """Return the repository path a job-level ``uses`` names, if it is local.

    A call is local when it names a path under the workflow directory. The
    path reader folds a leading `./` away, and the documented `$/` prefix is
    stripped first. Matching the shape rather than enumerating prefixes
    means a spelling nobody listed is not silently treated as remote. A
    remote call names `owner/repo/...@ref` and never starts there. A local
    path carrying `@ref` is refused: GitHub rejects it, and it is not a
    remote call this reader may skip.

    Returns
    -------
        The repository-relative path of a local callee, otherwise `None`.

    Raises
    ------
    ValueError
        When a local path carries a ref.

    Examples
    --------
    >>> local_callee("./.github/workflows/coverage.yml")
    '.github/workflows/coverage.yml'
    >>> local_callee("leynos/shared-actions/.github/workflows/x.yml@abc") is None
    True
    """
    text = uses.strip().removeprefix("$/")
    path = PurePosixPath(text)
    if _LOCAL_WORKFLOW_PREFIX not in path.parents:
        return None
    if "@" in text:
        msg = f"a local workflow call carries a ref: {uses}"
        raise ValueError(msg)
    return path.as_posix()


def pull_request_closure(found: cabc.Sequence[Workflow]) -> tuple[Workflow, ...]:
    """Return every workflow a pull request can run, called or triggered.

    A workflow declaring only `workflow_call` never matches a pull-request
    trigger, yet runs for one whenever a pull-request job calls it, with
    whatever secrets the call forwards. Every pull-request clause therefore
    ranges over this closure, not over the triggered workflows alone.

    Returns
    -------
        The reached workflows, sorted by path.
    """
    by_path = {workflow.path: workflow for workflow in found}
    pending = [w for w in found if serves_pull_requests(w.document)]
    reached: dict[str, Workflow] = {}
    while pending:
        workflow = pending.pop()
        if workflow.path in reached:
            continue
        reached[workflow.path] = workflow
        pending.extend(_called_workflows(workflow, by_path))
    return tuple(reached[path] for path in sorted(reached))


def _called_workflows(
    workflow: Workflow, by_path: cabc.Mapping[str, Workflow]
) -> list[Workflow]:
    """Return the local workflows ``workflow``'s jobs call.

    Returns
    -------
        The called workflows, in job order.

    Raises
    ------
    LookupError
        When a job calls a local workflow that does not exist, since the
        closure would otherwise stop short in silence.
    """
    callees = [
        callee
        for job in jobs(workflow).values()
        if isinstance(uses := job.get("uses"), str)
        and (callee := local_callee(uses)) is not None
    ]
    if missing := [callee for callee in callees if callee not in by_path]:
        msg = f"{workflow} calls {missing}, which does not exist"
        raise LookupError(msg)
    return [by_path[callee] for callee in callees]


def mentions(value: object, needle: str) -> bool:
    """Return whether ``needle`` appears in any key or scalar under ``value``.

    This reads every place a reference can live: `run` bodies, action
    inputs, `env` and `defaults` at any scope, `if` conditions, `secrets:`
    forwarding and a callee's `workflow_call` declarations. Matching is
    case-folded, since hosts and expression contexts are case-insensitive.

    Returns
    -------
        Whether any key or scalar contains ``needle``.
    """
    if isinstance(value, dict):
        return any(
            mentions(key, needle) or mentions(item, needle)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(mentions(item, needle) for item in value)
    return isinstance(value, str) and needle.casefold() in value.casefold()


def guard_conjuncts(condition: str) -> list[str] | None:
    """Split a step condition into its `&&` conjuncts, whitespace-normalized.

    An unquoted `||` yields `None`: one disjunct makes every conjunct
    optional, so no clause of such a guard can be relied upon. Single-quoted
    strings are skipped, including GitHub's doubled-quote escape, which
    toggles the state twice.

    Returns
    -------
        The conjuncts, or `None` when the condition has an unquoted `||`.

    Examples
    --------
    >>> guard_conjuncts("${{ a != '' && github.ref == 'refs/heads/main' }}")
    ["a != ''", "github.ref == 'refs/heads/main'"]
    >>> guard_conjuncts("a && b || c") is None
    True
    """
    body = condition.strip()
    if body.startswith("${{") and body.endswith("}}"):
        body = body[3:-2]
    operators = list(_unquoted_operators(body))
    if any(operator == "||" for _, operator in operators):
        return None
    # Each conjunct runs from just past one `&&` to the next; the sentinel
    # at -2 makes the first start at zero.
    bounds = [-2, *(index for index, _ in operators), len(body)]
    return [
        " ".join(body[start + 2 : end].split())
        for start, end in itertools.pairwise(bounds)
    ]


def _unquoted_operators(body: str) -> cabc.Iterator[tuple[int, str]]:
    """Yield the position and spelling of each `&&` or `||` outside quotes."""
    quoted = False
    index = 0
    while index < len(body):
        operator = body[index : index + 2]
        if body[index] == "'":
            quoted = not quoted
        elif not quoted and operator in {"&&", "||"}:
            yield index, operator
            index += 1
        index += 1


def cancels_in_progress(concurrency: object) -> bool:
    """Return whether a concurrency declaration may cancel a running job.

    Anything but an absent value or a literal false counts, so an expression
    or the string `'true'` is read as cancelling rather than as safe.

    Returns
    -------
        Whether the declaration may cancel a running job.
    """
    if not isinstance(concurrency, dict):
        return False
    value = concurrency.get("cancel-in-progress", False)
    return value is not False and str(value).strip().lower() != "false"


def uploaders(found: cabc.Sequence[Workflow]) -> tuple[Workflow, ...]:
    """Return every workflow that invokes the coverage uploader.

    Every uploader is counted, whatever its triggers, so a second one fails
    the sole-publisher clause instead of escaping it by pushing to another
    branch.

    Returns
    -------
        Every uploading workflow, in the order given.
    """
    return tuple(workflow for workflow in found if steps_using(workflow, UPLOAD_ACTION))


def is_trunk_publisher(document: cabc.Mapping[object, object]) -> bool:
    """Return whether a workflow pushes to `main` alone and serves no pull request.

    Both halves matter: `ci.yml` declares a push trigger too, and reading
    only that half would make one file required to upload and forbidden from
    uploading.

    Returns
    -------
        Whether both halves hold.
    """
    return pushes_to_main(document) and not serves_pull_requests(document)


# The contexts a concurrency group may interpolate: each resolves the same
# for every push to `main`, so successive publisher runs share one group.
# Anything else, `github.run_id` or `github.sha` above all, gives each run a
# group of its own, and runs in different groups do not wait for each other.
_STABLE_GROUP_CONTEXTS: typ.Final = frozenset({
    "github.workflow",
    "github.ref",
    "github.ref_name",
    "github.repository",
})


def unstable_group_expressions(concurrency: object) -> list[str]:
    """Return the expressions in a concurrency group that may vary per run.

    Returns
    -------
        Each interpolated expression outside the stable contexts, including
        compound ones, which this reader does not try to evaluate.
    """
    group = concurrency.get("group") if isinstance(concurrency, dict) else concurrency
    return [
        expression
        for expression in (
            " ".join(part.split("}}", 1)[0].split())
            for part in str(group or "").split("${{")[1:]
        )
        if expression not in _STABLE_GROUP_CONTEXTS
    ]


def reports_published_for_pull_requests(found: cabc.Sequence[Workflow]) -> list[str]:
    """Return the coverage steps a pull request can run that publish."""
    return [
        f"{workflow}: {step.get('name')}"
        for workflow in pull_request_closure(found)
        for step in coverage_steps(workflow)
        if publishes_report(step)
    ]


def token_references_for_pull_requests(found: cabc.Sequence[Workflow]) -> list[str]:
    """Return the workflows a pull request can run that may hold the credential.

    A workflow holds it when it names it anywhere, or when a job forwards
    `secrets: inherit` to a remote reusable workflow. The closure cannot
    read a remote workflow, so inheriting into one hands the credential
    over without its name appearing here.

    Returns
    -------
        The paths of the workflows that may hold it.
    """
    return [
        str(workflow)
        for workflow in pull_request_closure(found)
        if mentions(workflow.document, TOKEN_VARIABLE)
        or _inherits_into_remote(workflow)
    ]


def _inherits_into_remote(workflow: Workflow) -> bool:
    """Return whether a job forwards every secret to a remote workflow."""
    return any(
        isinstance(uses := job.get("uses"), str)
        and local_callee(uses) is None
        and job.get("secrets") == "inherit"
        for job in jobs(workflow).values()
    )


def uploads_for_pull_requests(found: cabc.Sequence[Workflow]) -> list[str]:
    """Return the workflows a pull request can run that invoke the uploader."""
    return [
        str(workflow)
        for workflow in pull_request_closure(found)
        if steps_using(workflow, UPLOAD_ACTION)
    ]


def host_references_for_pull_requests(found: cabc.Sequence[Workflow]) -> list[str]:
    """Return the workflows a pull request can run that name CodeScene's host."""
    return [
        str(workflow)
        for workflow in pull_request_closure(found)
        if mentions(workflow.document, CODESCENE_HOST)
    ]


def is_guarded_upload(condition: str) -> bool:
    """Return whether an upload condition requires the main ref and the token.

    Both must be whole conjuncts of a condition with no unquoted `||`. A
    substring test accepts `... && github.ref == 'refs/heads/main' ||
    github.event_name == 'workflow_dispatch'`, which uploads a dispatch from
    any branch.

    Returns
    -------
        Whether both guards are whole conjuncts of an `&&`-only condition.
    """
    conjuncts = guard_conjuncts(condition)
    if conjuncts is None:
        return False
    ref_guards = {MAIN_REF_GUARD, "'refs/heads/main' == github.ref"}
    token_guards = {f"{scope}.{TOKEN_VARIABLE} != ''" for scope in ("env", "secrets")}
    return bool(ref_guards.intersection(conjuncts)) and bool(
        token_guards.intersection(conjuncts)
    )


def unguarded_uploads(publisher: Workflow) -> dict[str, str]:
    """Return the publisher's upload steps whose condition is insufficient."""
    return {
        str(step.get("name")): condition
        for step in steps_using(publisher, UPLOAD_ACTION)
        if not is_guarded_upload(condition := str(step.get("if", "")))
    }


def cancelling_scopes(publisher: Workflow) -> list[str]:
    """Return the publisher's concurrency scopes that may cancel a run.

    A job-level block cancels as surely as the workflow-level one, so both
    are read.

    Returns
    -------
        The names of the scopes that may cancel, workflow first.
    """
    scopes = [("workflow", publisher.document.get("concurrency"))] + [
        (f"job {name!r}", job.get("concurrency"))
        for name, job in jobs(publisher).items()
    ]
    return [scope for scope, value in scopes if cancels_in_progress(value)]


# The one binding the upload step may hold, and the inputs that pass it on.
_TOKEN_BINDING: typ.Final = f"${{{{ secrets.{TOKEN_VARIABLE} }}}}"
_TOKEN_INPUTS: typ.Final = frozenset({
    f"${{{{ env.{TOKEN_VARIABLE} }}}}",
    _TOKEN_BINDING,
})


def _expression_text(value: object) -> str:
    """Return a scalar with the spacing inside `${{ }}` normalized."""
    return re.sub(r"\$\{\{\s*(.*?)\s*\}\}", r"${{ \1 }}", str(value).strip())


def _input(step: cabc.Mapping[str, object], scope: str, name: str) -> str:
    """Return one normalized value from a step's ``env`` or ``with`` block."""
    block = step.get(scope)
    return _expression_text(block.get(name, "")) if isinstance(block, dict) else ""


def unbound_uploads(publisher: Workflow) -> dict[str, str]:
    """Return the upload steps that do not bind and pass the credential.

    A guard on `env.CS_ACCESS_TOKEN != ''` passes with the binding deleted,
    and the upload then skips on every run in silence. So the step itself
    must bind the variable from the secret and hand it to the action's
    `access-token` input.

    Returns
    -------
        Each offending step's name and what it lacks.
    """
    lacking = {
        str(step.get("name")): [
            gap
            for gap, holds in (
                (
                    f"env.{TOKEN_VARIABLE} bound to the secret",
                    _input(step, "env", TOKEN_VARIABLE) == _TOKEN_BINDING,
                ),
                (
                    "access-token passing it on",
                    _input(step, "with", "access-token") in _TOKEN_INPUTS,
                ),
            )
            if not holds
        ]
        for step in steps_using(publisher, UPLOAD_ACTION)
    }
    return {name: ", ".join(gaps) for name, gaps in lacking.items() if gaps}
