"""Contract tests for the coverage topology CV-005 describes.

`main` owns both persistent coverage outputs: the CodeScene upload and the
ratchet baseline every pull request is measured against. The properties
below are not enforced by the workflows themselves, and each fails quietly
rather than loudly:

* a pull-request lane that omits `publish-artefact` publishes its report,
  because the shared action defaults the input to `"true"`;
* anything a pull request can run that holds the CodeScene credential,
  invokes the uploader or calls CodeScene's host makes the pull request a
  second publisher, or a second caller of an API whose answer has changed
  shape under us twice;
* the publisher's upload step guarded only on the credential would publish a
  feature branch's coverage as the trunk's the first time someone dispatches
  the workflow from that branch, since a `workflow_dispatch` selects its own
  ref and the push filter says nothing about it;
* two pushes to `main` in quick succession would race to write the baseline,
  and a cancelled publisher abandons both its upload and its baseline write.

"What a pull request can run" is the transitive closure of the
pull-request-triggered workflows through local reusable-workflow calls: a
workflow declaring only `workflow_call` never matches a pull-request trigger,
yet runs for one whenever a pull-request job calls it with `secrets:
inherit`. The readers live in `coverage_topology_support`, and
`test_coverage_topology_readers` drives them against synthetic documents,
because a rule cannot be proved by the files that already comply with it.

The full rule lives in the `main-owned-codescene-coverage` rule package and
judges more than this: the clauses here are the ones this repository was
missing.
"""

from __future__ import annotations

import typing as typ

from tests.unit.coverage_topology_support import (
    MAIN_REF_GUARD,
    TOKEN_VARIABLE,
    Workflow,
    cancelling_scopes,
    coverage_steps,
    host_references_for_pull_requests,
    is_trunk_publisher,
    pull_request_closure,
    reports_published_for_pull_requests,
    token_references_for_pull_requests,
    unguarded_uploads,
    unstable_group_expressions,
    uploaders,
    uploads_for_pull_requests,
    workflows,
)


def _repository_workflows() -> tuple[Workflow, ...]:
    """Return this repository's workflows, refusing an empty directory."""
    found = workflows()
    assert found, (
        "no workflow files were found, so every clause below would pass vacuously"
    )
    return found


def _sole_publisher() -> Workflow:
    """Return the single uploader, refusing any other count or any other trigger.

    Every uploader is counted before its triggers are judged, so a second
    one cannot escape the count by pushing to another branch.

    Returns
    -------
        The one uploading workflow.
    """
    found = uploaders(_repository_workflows())
    assert len(found) == 1, (
        "exactly one workflow must upload coverage, so that one baseline and "
        f"one upload exist; found {[str(w) for w in found]}"
    )
    (publisher,) = found
    assert is_trunk_publisher(publisher.document), (
        f"{publisher} must run on pushes to main alone and serve no pull request"
    )
    return publisher


def test_the_trunk_has_exactly_one_coverage_publisher() -> None:
    """One workflow owns the upload and the baseline, and it is enumerated."""
    publisher = _sole_publisher()
    assert publisher.path == ".github/workflows/coverage-main.yml", (
        f"the trunk publisher moved to {publisher}; if that is intended, this "
        "contract's other clauses should be read against the new file"
    )


def test_the_pull_request_lanes_generate_coverage() -> None:
    """The pull-request clauses range over a non-empty set.

    Deleting every pull-request coverage step would satisfy them otherwise,
    and the ratchet it feeds would go with it.
    """
    generating = [
        str(workflow)
        for workflow in pull_request_closure(_repository_workflows())
        if coverage_steps(workflow)
    ]
    assert generating, (
        "no workflow a pull request runs generates coverage, so the clauses "
        "below would pass vacuously and no lane would run the ratchet"
    )


def test_every_pull_request_coverage_step_keeps_its_report_local() -> None:
    """Only the trunk publisher publishes the coverage report.

    A lane that omits `publish-artefact` publishes it, because the action
    defaults the input to `"true"`, so the effective value is read.
    """
    publishing = reports_published_for_pull_requests(_repository_workflows())
    assert not publishing, (
        "a coverage step a pull request can run must set publish-artefact to "
        f"false; these publish the report: {publishing}"
    )


def test_nothing_a_pull_request_runs_holds_the_credential() -> None:
    """The CodeScene credential is named only where the trunk publishes.

    Every scope is read: `run` bodies, action inputs, `env` at any level and
    `secrets:` forwarding, across called workflows as well as triggered ones.
    """
    holding = token_references_for_pull_requests(_repository_workflows())
    assert not holding, (
        f"{TOKEN_VARIABLE} must not be reachable from a pull request; "
        f"these workflows name it: {holding}"
    )


def test_nothing_a_pull_request_runs_contacts_codescene() -> None:
    """Pull requests neither invoke the uploader nor name CodeScene's host."""
    found = _repository_workflows()
    uploading = uploads_for_pull_requests(found)
    calling = host_references_for_pull_requests(found)
    assert not uploading, (
        f"a pull request must not run the CodeScene uploader; found {uploading}"
    )
    assert not calling, (
        f"a pull request must not call CodeScene's host; found {calling}"
    )


def test_the_publisher_guards_its_upload_on_the_ref_and_the_token() -> None:
    """A dispatch from a branch must not publish that branch as the trunk.

    The push filter constrains the push event only. A `workflow_dispatch`
    selects its own ref, so the ref is a whole conjunct of the step's
    condition, and a condition with any unquoted `||` is refused.
    """
    publisher = _sole_publisher()
    unguarded = unguarded_uploads(publisher)
    assert not unguarded, (
        f"every upload step in {publisher} must be guarded on both "
        f"{MAIN_REF_GUARD} and {TOKEN_VARIABLE}, joined by && alone; "
        f"found {unguarded}"
    )


def test_the_publisher_serializes_its_baseline_writes() -> None:
    """Two pushes to main must not race to write one baseline.

    The loser of that race leaves a partial write, and a pull request
    restoring it is measured against a baseline no run ever finished.
    """
    publisher = _sole_publisher()
    concurrency = publisher.document.get("concurrency")
    assert concurrency, (
        f"{publisher} must declare a concurrency block, so overlapping pushes "
        "to main cannot both write the ratchet baseline"
    )
    if isinstance(concurrency, dict):
        assert concurrency.get("group"), (
            f"{publisher}'s concurrency block must name a group; found {concurrency}"
        )
    unstable = unstable_group_expressions(concurrency)
    assert not unstable, (
        f"{publisher}'s concurrency group must resolve the same for every push "
        f"to main, or runs in different groups race; these vary: {unstable}"
    )


def test_the_publisher_queues_rather_than_cancels() -> None:
    """A cancelled publisher abandons its upload and its baseline write.

    A queued one merely publishes later, and the later push's baseline is
    the one that should win. Job-level blocks cancel as surely as the
    workflow-level one.
    """
    publisher = _sole_publisher()
    cancelling = cancelling_scopes(publisher)
    assert not cancelling, (
        f"{publisher} must not set cancel-in-progress; found it in {cancelling}"
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
        for workflow in _repository_workflows()
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
