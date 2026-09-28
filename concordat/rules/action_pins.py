"""Facts about whether a GitHub Action pin names a commit or an annotated tag.

A 40-hex `uses:` ref looks like a commit pin whatever object it names. An
annotated tag has its own object SHA, and GitHub Actions accepts it, so a
workflow pinned to one runs green while pinning a mutable tag rather than
the commit the baseline asks for. Only the action repository's object store
can tell the two apart, so the answer is recorded as a fact in the envelope
and the policy judges it.

This module is the transport-free half: the resolution type, the resolver
signature, and the collection of full-SHA pins from decoded workflows. The
GitHub adapter lives in `concordat.rules.github_pins`; the rule-run command
composes it, and tests or the fixture generator pass their own resolvers. A
resolver never raises for an ordinary failure: a refusal, an unknown object or
an unreachable API is an unresolved pin carrying its reason, which the policy
reports as indeterminate rather than as a pass.
"""

from __future__ import annotations

import re
import typing as typ

if typ.TYPE_CHECKING:
    import collections.abc as cabc

# A pin the policy treats as a full SHA; anything else is judged as a
# floating ref by the policy itself and is not resolved here.
FULL_SHA: typ.Final = re.compile(r"^[0-9a-f]{40}$")

OBJECT_COMMIT: typ.Final = "commit"
OBJECT_TAG: typ.Final = "tag"


class PinResolution(typ.TypedDict):
    """What one pinned SHA names in the action's repository.

    ``object_type`` is ``"commit"``, ``"tag"`` (an annotated tag object), or
    ``None`` when the pin could not be resolved. ``commit`` is the commit the
    pin reaches: the pin itself for a commit, the peeled target for a tag,
    and ``None`` otherwise. ``error`` carries the reason a pin stayed
    unresolved.
    """

    object_type: str | None
    commit: str | None
    error: str | None


type PinResolver = cabc.Callable[[str, str], PinResolution]


def commit_pin(sha: str) -> PinResolution:
    """Return the resolution of a pin that names a commit."""
    return PinResolution(object_type=OBJECT_COMMIT, commit=sha, error=None)


def tag_pin(commit: str | None) -> PinResolution:
    """Return the resolution of a pin that names an annotated tag object."""
    return PinResolution(object_type=OBJECT_TAG, commit=commit, error=None)


def unresolved_pin(reason: str) -> PinResolution:
    """Return the resolution of a pin whose object could not be determined."""
    return PinResolution(object_type=None, commit=None, error=reason)


def _steps(workflow: cabc.Mapping[str, object]) -> cabc.Iterator[object]:
    """Yield every step mapping of a decoded workflow, skipping malformed parts."""
    jobs = workflow.get("parsed")
    jobs = jobs.get("jobs") if isinstance(jobs, dict) else None
    if not isinstance(jobs, dict):
        return
    for job in jobs.values():
        steps = job.get("steps") if isinstance(job, dict) else None
        if isinstance(steps, list):
            yield from steps


def _step_uses(step: object) -> str | None:
    """Return a step's textual ``uses:`` value, or ``None`` when it has none."""
    uses = step.get("uses") if isinstance(step, dict) else None
    return uses if isinstance(uses, str) else None


def _uses_refs(
    workflows: cabc.Iterable[cabc.Mapping[str, object]],
) -> cabc.Iterator[str]:
    """Yield the ``uses:`` value of every step that has a textual one."""
    for workflow in workflows:
        refs = (_step_uses(step) for step in _steps(workflow))
        yield from (uses for uses in refs if uses is not None)


def _full_sha_ref(uses: str, prefix: str) -> str | None:
    """Return the full-SHA ref of *uses* when it pins the prefixed action."""
    ref = uses.removeprefix(prefix)
    return ref if uses.startswith(prefix) and FULL_SHA.match(ref) else None


def pinned_shas(
    workflows: cabc.Iterable[cabc.Mapping[str, object]], action: str
) -> list[str]:
    """Return every distinct full-SHA ref of *action* the workflows use, sorted."""
    prefix = f"{action}@"
    refs = (_full_sha_ref(uses, prefix) for uses in _uses_refs(workflows))
    return sorted({ref for ref in refs if ref is not None})


def resolve_pins(
    workflows: cabc.Iterable[cabc.Mapping[str, object]],
    action: str,
    resolver: PinResolver,
) -> dict[str, PinResolution]:
    """Return the resolution of every full-SHA pin of *action*, keyed by SHA."""
    return {sha: resolver(action, sha) for sha in pinned_shas(workflows, action)}
