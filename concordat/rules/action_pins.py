"""Resolve whether a GitHub Action pin names a commit or an annotated tag.

A 40-hex `uses:` ref looks like a commit pin whatever object it names. An
annotated tag has its own object SHA, and GitHub Actions accepts it, so a
workflow pinned to one runs green while pinning a mutable tag rather than
the commit the baseline asks for. Only the repository's object store can tell
the two apart, so the resolution is recorded as a fact in the envelope and the
policy judges it.

Resolution is injected. The production resolver asks the GitHub REST API
(`git/commits/{sha}`, then `git/tags/{sha}`); the fixture generator and the
unit tests answer from a fixed table. A resolver never raises: a refusal, an unknown
object or an unreachable API is reported as an unresolved pin carrying its
reason, which the policy treats as indeterminate rather than as a pass.
"""

from __future__ import annotations

import re
import typing as typ

import requests

if typ.TYPE_CHECKING:
    import collections.abc as cabc

DEFAULT_API_URL: typ.Final = "https://api.github.com"
REQUEST_TIMEOUT_SECONDS: typ.Final = 15

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


def _get(
    session: requests.Session, url: str
) -> tuple[int | None, dict[str, object] | None, str | None]:
    """Fetch *url*, returning its status, decoded body and any failure."""
    try:
        response = session.get(url, timeout=REQUEST_TIMEOUT_SECONDS)
    except requests.RequestException as error:
        return None, None, f"request failed: {error}"
    if response.status_code != 200:
        return response.status_code, None, None
    try:
        body = response.json()
    except ValueError as error:
        return response.status_code, None, f"undecodable response: {error}"
    return (
        (response.status_code, body, None)
        if isinstance(body, dict)
        else (
            response.status_code,
            None,
            "response is not a JSON object",
        )
    )


def _peeled_commit(body: dict[str, object]) -> str | None:
    """Return the commit an annotated tag points at, if it points at one."""
    target = body.get("object")
    if not isinstance(target, dict) or target.get("type") != OBJECT_COMMIT:
        return None
    sha = target.get("sha")
    return sha if isinstance(sha, str) else None


def github_resolver(
    *,
    token: str | None = None,
    api_url: str = DEFAULT_API_URL,
    session: requests.Session | None = None,
) -> PinResolver:
    """Return a resolver that asks the GitHub REST API what a pin names.

    The commit endpoint is asked first; only when it does not know the SHA is
    the tag endpoint asked. Any failure along the way, including a refusal or
    rate limit, leaves the pin unresolved with the reason.

    Returns
    -------
    PinResolver
        A resolver bound to one session, and to *token* when one is given.
    """
    client = session or requests.Session()
    client.headers.update({
        "Accept": "application/vnd.github+json",
        "User-Agent": "concordat-rule-runner",
    })
    if token:
        client.headers["Authorization"] = f"Bearer {token}"
    base = api_url.rstrip("/")

    def resolve(repository: str, sha: str) -> PinResolution:
        status, body, failure = _get(
            client, f"{base}/repos/{repository}/git/commits/{sha}"
        )
        if failure is not None:
            return unresolved_pin(failure)
        if body is not None:
            return commit_pin(sha)
        if status not in {404, 422}:
            return unresolved_pin(f"git/commits/{sha} returned HTTP {status}")
        status, body, failure = _get(
            client, f"{base}/repos/{repository}/git/tags/{sha}"
        )
        if failure is not None:
            return unresolved_pin(failure)
        if body is None:
            return unresolved_pin(
                f"{sha} is neither a commit nor a tag (HTTP {status})"
            )
        return tag_pin(_peeled_commit(body))

    return resolve


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


def pinned_shas(
    workflows: cabc.Iterable[cabc.Mapping[str, object]], action: str
) -> list[str]:
    """Return every distinct full-SHA ref of *action* the workflows use, sorted."""
    prefix = f"{action}@"
    found = set()
    for workflow in workflows:
        for step in _steps(workflow):
            uses = step.get("uses") if isinstance(step, dict) else None
            if isinstance(uses, str) and uses.startswith(prefix):
                ref = uses.removeprefix(prefix)
                if FULL_SHA.match(ref):
                    found.add(ref)
    return sorted(found)


def resolve_pins(
    workflows: cabc.Iterable[cabc.Mapping[str, object]],
    action: str,
    resolver: PinResolver,
) -> dict[str, PinResolution]:
    """Return the resolution of every full-SHA pin of *action*, keyed by SHA."""
    return {sha: resolver(action, sha) for sha in pinned_shas(workflows, action)}
