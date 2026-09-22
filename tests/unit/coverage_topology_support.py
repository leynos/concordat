"""Readers for the coverage topology contract.

`test_coverage_topology_contract` judges this repository's workflows and
`test_coverage_topology_readers` drives these readers against synthetic
documents, so both see the topology through the same eyes. Nothing here
asserts a repository fact; shape errors raise, so a document these readers
cannot interpret fails loudly rather than reading as compliant.
"""

from __future__ import annotations

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

    Returns
    -------
        The trigger mapping, empty for a workflow with no triggers.

    Raises
    ------
    TypeError
        When the trigger value is neither a name, a list of names, nor a
        mapping.
    """
    for key in _TRIGGER_KEYS:
        if key not in document:
            continue
        value = document[key]
        if isinstance(value, dict):
            return typ.cast("dict[str, object]", value)
        if isinstance(value, str):
            return {value: None}
        if isinstance(value, list) and all(isinstance(name, str) for name in value):
            return {typ.cast("str", name): None for name in value}
        msg = f"unrecognized trigger form: {value!r}"
        raise TypeError(msg)
    return {}


def serves_pull_requests(document: cabc.Mapping[object, object]) -> bool:
    """Return whether a workflow is triggered by a pull-request event."""
    return any(name.startswith("pull_request") for name in triggers(document))


def pushes_to_main(document: cabc.Mapping[object, object]) -> bool:
    """Return whether a workflow runs on a push restricted to `main`."""
    push = triggers(document).get("push")
    if not isinstance(push, dict):
        return False
    branches = push.get("branches")
    if isinstance(branches, str):
        return branches == "main"
    return isinstance(branches, list) and "main" in branches


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

    A call is local when it names a path under the workflow directory; the
    path reader already folds a leading `./` away. Matching the shape rather
    than enumerating prefixes means a spelling nobody listed is not silently
    treated as remote. A remote call names `owner/repo/...@ref` and never
    starts there.

    Returns
    -------
        The repository-relative path of a local callee, otherwise `None`.

    Examples
    --------
    >>> local_callee("./.github/workflows/coverage.yml")
    '.github/workflows/coverage.yml'
    >>> local_callee("leynos/shared-actions/.github/workflows/x.yml@abc") is None
    True
    """
    path = PurePosixPath(uses.strip())
    if _LOCAL_WORKFLOW_PREFIX in path.parents:
        return path.as_posix()
    return None


def pull_request_closure(found: cabc.Sequence[Workflow]) -> tuple[Workflow, ...]:
    """Return every workflow a pull request can run, called or triggered.

    A workflow declaring only `workflow_call` never matches a pull-request
    trigger, yet runs for one whenever a pull-request job calls it, with
    whatever secrets the call forwards. Every pull-request clause therefore
    ranges over this closure, not over the triggered workflows alone.

    Returns
    -------
        The reached workflows, sorted by path.

    Raises
    ------
    LookupError
        When a reached job calls a local workflow that does not exist, since
        the closure would otherwise stop short in silence.
    """
    by_path = {workflow.path: workflow for workflow in found}
    pending = [w for w in found if serves_pull_requests(w.document)]
    reached: dict[str, Workflow] = {}
    while pending:
        workflow = pending.pop()
        if workflow.path in reached:
            continue
        reached[workflow.path] = workflow
        for job in jobs(workflow).values():
            uses = job.get("uses")
            callee = local_callee(uses) if isinstance(uses, str) else None
            if callee is None:
                continue
            if callee not in by_path:
                msg = f"{workflow} calls {callee}, which does not exist"
                raise LookupError(msg)
            pending.append(by_path[callee])
    return tuple(reached[path] for path in sorted(reached))


def mentions(value: object, needle: str) -> bool:
    """Return whether ``needle`` appears in any key or scalar under ``value``.

    This reads every place a reference can live: `run` bodies, action
    inputs, `env` at any scope, `if` conditions and `secrets:` forwarding.

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
    return isinstance(value, str) and needle in value


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
    parts: list[str] = []
    start = 0
    quoted = False
    index = 0
    while index < len(body):
        if body[index] == "'":
            quoted = not quoted
        elif not quoted and body.startswith("||", index):
            return None
        elif not quoted and body.startswith("&&", index):
            parts.append(body[start:index])
            start = index + 2
            index += 1
        index += 1
    parts.append(body[start:])
    return [" ".join(part.split()) for part in parts]


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


def publishers(found: cabc.Sequence[Workflow]) -> tuple[Workflow, ...]:
    """Return the workflows that publish coverage from the trunk.

    A publisher pushes to `main` and serves no pull request. Both halves
    matter: `ci.yml` declares a push trigger too, and reading only that half
    would make one file required to upload and forbidden from uploading.

    Returns
    -------
        Every workflow that uploads coverage on a push to `main`.
    """
    return tuple(
        workflow
        for workflow in found
        if pushes_to_main(workflow.document)
        and not serves_pull_requests(workflow.document)
        and steps_using(workflow, UPLOAD_ACTION)
    )


def reports_published_for_pull_requests(found: cabc.Sequence[Workflow]) -> list[str]:
    """Return the coverage steps a pull request can run that publish."""
    return [
        f"{workflow}: {step.get('name')}"
        for workflow in pull_request_closure(found)
        for step in coverage_steps(workflow)
        if publishes_report(step)
    ]


def token_references_for_pull_requests(found: cabc.Sequence[Workflow]) -> list[str]:
    """Return the workflows a pull request can run that name the credential."""
    return [
        str(workflow)
        for workflow in pull_request_closure(found)
        if mentions(workflow.document, TOKEN_VARIABLE)
    ]


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
