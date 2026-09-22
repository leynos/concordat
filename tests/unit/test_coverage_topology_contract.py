"""Contract tests for the coverage topology CV-005 describes.

`main` owns both persistent coverage outputs: the CodeScene upload and the
ratchet baseline every pull request is measured against. Three properties of
that topology are not enforced by the workflows themselves, and each fails
quietly rather than loudly:

* a pull-request lane that omits `publish-artefact` publishes its report,
  because the shared action defaults the input to `"true"`. Nothing fails;
  the repository simply grows a second publisher of the same artefact;
* the publisher's upload step guarded only on the credential would publish a
  feature branch's coverage as the trunk's the first time someone dispatches
  the workflow from that branch, since a `workflow_dispatch` selects its own
  ref and the push filter says nothing about it;
* two pushes to `main` in quick succession would race to write the baseline,
  and the loser's partial write is the one a pull request might restore.

The contract reads the effective value rather than the spelling, so omitting
an input fails where the default is wrong. It enumerates workflows rather
than naming `ci.yml` and `coverage-main.yml`, so a workflow added later is
covered the day it appears, and it reads the trigger mapping under both the
`on` key and the boolean `True` that unquoted YAML produces, because a reader
that knows only one of those silently ranges over an empty set.

The full rule lives in the `main-owned-codescene-coverage` rule package and
judges more than this: the clauses here are the ones this repository was
missing.
"""

from __future__ import annotations

import typing as typ
from pathlib import Path

from ruamel.yaml import YAML

if typ.TYPE_CHECKING:
    import collections.abc as cabc

REPOSITORY_ROOT: typ.Final = Path(__file__).parents[2]
WORKFLOW_DIRECTORY: typ.Final = REPOSITORY_ROOT / ".github/workflows"

_COVERAGE_ACTION: typ.Final = "generate-coverage@"
_UPLOAD_ACTION: typ.Final = "upload-codescene-coverage@"
_MAIN_REF: typ.Final = "refs/heads/main"
# The credential's name, not a credential: the contract asserts that the
# upload step mentions it in its guard.
_TOKEN_VARIABLE: typ.Final = "CS_ACCESS_TOKEN"  # noqa: S105 - a variable name

# The two spellings a trigger mapping arrives under. YAML 1.1 reads an
# unquoted `on:` as the boolean true, so a reader that looks only for the
# string key finds nothing and every clause below passes vacuously.
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
    assert isinstance(value, dict), f"expected {subject} to be a mapping"
    return typ.cast("dict[str, object]", value)


def _document(value: object, *, subject: str) -> dict[object, object]:
    """Return a workflow document, whose keys need not all be strings."""
    assert isinstance(value, dict), f"expected {subject} to be a mapping"
    return typ.cast("dict[object, object]", value)


def workflows() -> tuple[Workflow, ...]:
    """Return every parsed workflow in this repository.

    Returns
    -------
        One entry per workflow document, sorted by path.
    """
    yaml = YAML(typ="safe")
    paths = sorted(
        path
        for pattern in ("*.yml", "*.yaml")
        for path in WORKFLOW_DIRECTORY.glob(pattern)
    )
    assert paths, (
        "no workflow files were found, so every clause below would pass vacuously"
    )
    return tuple(
        Workflow(
            path.relative_to(REPOSITORY_ROOT).as_posix(),
            _document(
                yaml.load(path.read_text(encoding="utf-8")),
                subject=f"{path.name} workflow",
            ),
        )
        for path in paths
    )


def triggers(document: cabc.Mapping[object, object]) -> dict[str, object]:
    """Return a workflow's trigger mapping, under either spelling of the key.

    Parameters
    ----------
    document:
        One parsed workflow document.

    Returns
    -------
        The trigger mapping, or an empty one for a workflow with no triggers
        this reader understands.
    """
    for key in _TRIGGER_KEYS:
        if key in document:
            value = document[key]
            if isinstance(value, dict):
                return typ.cast("dict[str, object]", value)
            # `on: push` and `on: [push, pull_request]` carry no filters.
            if isinstance(value, str):
                return {value: None}
            if isinstance(value, list):
                return {name: None for name in value if isinstance(name, str)}
    return {}


def serves_pull_requests(document: cabc.Mapping[object, object]) -> bool:
    """Return whether a workflow runs for pull requests."""
    return any(name.startswith("pull_request") for name in triggers(document))


def pushes_to_main(document: cabc.Mapping[object, object]) -> bool:
    """Return whether a workflow runs on a push restricted to `main`.

    Returns
    -------
        Whether the push trigger exists and its branch filter names `main`.
    """
    push = triggers(document).get("push")
    if not isinstance(push, dict):
        return False
    branches = push.get("branches")
    if isinstance(branches, str):
        return branches == "main"
    return isinstance(branches, list) and "main" in branches


def _steps(
    document: cabc.Mapping[object, object], *, subject: str
) -> list[dict[str, object]]:
    """Return every step of every job in a workflow, in document order."""
    jobs = document.get("jobs")
    if not isinstance(jobs, dict):
        return []
    collected: list[dict[str, object]] = []
    for name, job in jobs.items():
        job_mapping = _mapping(job, subject=f"{subject} job {name!r}")
        steps = job_mapping.get("steps")
        if not isinstance(steps, list):
            continue
        collected.extend(
            _mapping(step, subject=f"{subject} job {name!r} step") for step in steps
        )
    return collected


def _steps_using(workflow: Workflow, action: str) -> list[dict[str, object]]:
    """Return the workflow's steps that invoke ``action``."""
    return [
        step
        for step in _steps(workflow.document, subject=str(workflow))
        if isinstance(uses := step.get("uses"), str) and action in uses
    ]


def coverage_steps(workflow: Workflow) -> list[dict[str, object]]:
    """Return the workflow's `generate-coverage` steps."""
    return _steps_using(workflow, _COVERAGE_ACTION)


def publishes_report(step: cabc.Mapping[str, object]) -> bool:
    """Return whether a coverage step publishes its report as an artefact.

    The action defaults the input to `"true"`, so a step that omits it
    publishes. The effective value is what this reads, not the spelling.

    Parameters
    ----------
    step:
        One `generate-coverage` step.

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


def publishers() -> tuple[Workflow, ...]:
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
        for workflow in workflows()
        if pushes_to_main(workflow.document)
        and not serves_pull_requests(workflow.document)
        and _steps_using(workflow, _UPLOAD_ACTION)
    )


def _sole_publisher() -> Workflow:
    """Return the single trunk publisher, refusing any other count."""
    found = publishers()
    assert len(found) == 1, (
        "exactly one workflow must upload coverage from the trunk, so that "
        f"one baseline and one upload exist; found {[str(w) for w in found]}"
    )
    return found[0]


def test_the_trunk_has_exactly_one_coverage_publisher() -> None:
    """One workflow owns the upload and the baseline, and it is enumerated."""
    publisher = _sole_publisher()
    assert publisher.path == ".github/workflows/coverage-main.yml", (
        f"the trunk publisher moved to {publisher}; if that is intended, this "
        "contract's other clauses should be read against the new file"
    )


def test_every_pull_request_coverage_step_keeps_its_report_local() -> None:
    """Only the trunk publisher publishes the coverage report.

    A lane that omits `publish-artefact` publishes it, because the action
    defaults the input to `"true"`, so this reads the effective value.
    """
    publishing = {
        str(workflow): [step.get("name") for step in steps if publishes_report(step)]
        for workflow in workflows()
        if serves_pull_requests(workflow.document)
        and (steps := coverage_steps(workflow))
        and any(publishes_report(step) for step in steps)
    }
    assert not publishing, (
        "a pull-request coverage step must set publish-artefact to false; "
        f"these publish the report: {publishing}"
    )


def test_the_pull_request_lanes_generate_coverage() -> None:
    """The clause above ranges over a non-empty set.

    Deleting every pull-request coverage step would satisfy it otherwise,
    and the ratchet it feeds would go with it.
    """
    generating = [
        str(workflow)
        for workflow in workflows()
        if serves_pull_requests(workflow.document) and coverage_steps(workflow)
    ]
    assert generating, (
        "no pull-request workflow generates coverage, so the locality clause "
        "would pass vacuously and no lane would run the ratchet"
    )


def test_the_publisher_guards_its_upload_on_the_ref_and_the_token() -> None:
    """A dispatch from a branch must not publish that branch as the trunk.

    The push filter constrains the push event only. A `workflow_dispatch`
    selects its own ref, so the ref is checked on the step as well as the
    credential.
    """
    publisher = _sole_publisher()
    uploads = _steps_using(publisher, _UPLOAD_ACTION)
    assert uploads, f"{publisher} was enumerated as the publisher but uploads nothing"
    unguarded = {
        step.get("name"): condition
        for step in uploads
        if not (
            _MAIN_REF in (condition := str(step.get("if", "")))
            and "github.ref" in condition
        )
        or _TOKEN_VARIABLE not in condition
    }
    assert not unguarded, (
        f"every upload step in {publisher} must be guarded on both "
        f"github.ref == '{_MAIN_REF}' and {_TOKEN_VARIABLE}; found {unguarded}"
    )


def test_the_publisher_serializes_its_baseline_writes() -> None:
    """Two pushes to main must not race to write one baseline.

    The loser of that race leaves a partial write, and a pull request
    restoring it is measured against a baseline no run ever finished.
    """
    publisher = _sole_publisher()
    concurrency = publisher.document.get("concurrency")
    assert concurrency is not None, (
        f"{publisher} must declare a concurrency block, so overlapping pushes "
        "to main cannot both write the ratchet baseline"
    )
    if isinstance(concurrency, dict):
        assert concurrency.get("group"), (
            f"{publisher}'s concurrency block must name a group; found {concurrency}"
        )
        assert concurrency.get("cancel-in-progress") is not True, (
            f"{publisher} must queue rather than cancel: a cancelled publisher "
            "abandons its upload and its baseline write, where a queued one "
            "publishes later"
        )
    else:
        assert str(concurrency).strip(), (
            f"{publisher}'s concurrency value must not be empty"
        )


def test_the_coverage_action_is_pinned_identically_in_every_lane() -> None:
    """One pin measures coverage everywhere, or the ratchet compares two rulers.

    The publisher writes the baseline that the pull-request lanes are
    measured against, so a lane on a different pin can fail a ratchet for a
    change in the measurement rather than in the diff.
    """
    pins = {
        str(workflow): sorted({
            typ.cast("str", step["uses"]).split("@", 1)[1]
            for step in coverage_steps(workflow)
        })
        for workflow in workflows()
        if coverage_steps(workflow)
    }
    assert pins, (
        "no workflow invokes the coverage action, so the pin assertion would "
        "pass vacuously"
    )
    distinct = {pin for pin_list in pins.values() for pin in pin_list}
    assert len(distinct) == 1, (
        f"every lane must invoke one pinned coverage action; found {pins}"
    )


def test_an_unquoted_trigger_key_is_still_read() -> None:
    """The trigger reader knows both spellings of the key.

    YAML 1.1 reads an unquoted `on:` as the boolean true. A reader that knows
    only the string key finds no triggers at all, and every clause that
    filters workflows by trigger then ranges over an empty set and passes.
    """
    quoted: dict[object, object] = {"on": {"push": {"branches": ["main"]}}}
    unquoted: dict[object, object] = {True: {"push": {"branches": ["main"]}}}
    assert triggers(quoted) == triggers(unquoted), (
        "both spellings of the trigger key must read the same"
    )
    assert pushes_to_main(unquoted), (
        "a workflow whose trigger key parsed as a boolean still pushes to main"
    )
    assert not serves_pull_requests(unquoted), (
        "this fixture has no pull-request trigger"
    )
    assert serves_pull_requests({True: {"pull_request": None}}), (
        "a pull-request trigger must be recognized under the boolean key too"
    )


def test_a_publisher_must_not_also_serve_pull_requests() -> None:
    """The publisher predicate reads both halves, not just the push trigger.

    `ci.yml` declares a push trigger as well, so a predicate reading only
    that half would make one file both required to upload and forbidden from
    uploading.
    """
    both: dict[object, object] = {
        "on": {"push": {"branches": ["main"]}, "pull_request": None}
    }
    assert pushes_to_main(both), "the fixture does push to main"
    assert serves_pull_requests(both), "and it does serve pull requests"
    only_push: dict[object, object] = {"on": {"push": {"branches": ["main"]}}}
    assert not serves_pull_requests(only_push), (
        "a push-only workflow serves no pull request"
    )
    assert not pushes_to_main({"on": {"push": {"branches": ["release"]}}}), (
        "a push filtered to another branch is not a trunk push"
    )


def test_an_omitted_artefact_input_counts_as_publishing() -> None:
    """The effective value is read, not the spelling.

    A contract that looked for `publish-artefact: 'false'` textually would be
    satisfied by a lane that omits the input, which is the lane that
    publishes.
    """
    assert publishes_report({"with": {}}), "an omitted input takes the action's default"
    assert publishes_report({}), "a step with no inputs takes it too"
    assert publishes_report({"with": {"publish-artefact": "true"}}), (
        "an explicit true publishes"
    )
    assert not publishes_report({"with": {"publish-artefact": "false"}}), (
        "an explicit false does not"
    )
    assert not publishes_report({"with": {"publish-artefact": False}}), (
        "an unquoted false is a boolean, and does not publish either"
    )
