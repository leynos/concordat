"""Drive the coverage topology readers against synthetic workflows.

The repository's own workflows comply, so a contract run over them passes
whether or not a reader can see the hazard it exists for. Each case here
builds the hazard directly and requires the reader to see it.
"""

from __future__ import annotations

import textwrap

import pytest
from ruamel.yaml.constructor import DuplicateKeyError

from tests.unit.coverage_topology_support import (
    Workflow,
    cancelling_scopes,
    host_references_for_pull_requests,
    is_guarded_upload,
    is_trunk_publisher,
    local_callee,
    parse_workflow,
    publishes_report,
    pull_request_closure,
    pushes_to_main,
    reports_published_for_pull_requests,
    serves_pull_requests,
    token_references_for_pull_requests,
    triggers,
    unbound_uploads,
    unstable_group_expressions,
    uploads_for_pull_requests,
)

_PULL_REQUEST_CALLER = """
    on:
      pull_request:
    jobs:
      coverage:
        uses: {callee}
        secrets: inherit
"""

# The episodic probe: a reusable workflow that no pull-request trigger names,
# reached through a pull-request job, spending the inherited credential
# against CodeScene's API.
_CALLED_ONLY = """
    on:
      workflow_call:
    jobs:
      probe:
        runs-on: ubuntu-latest
        steps:
          - name: Ask CodeScene
            run: >-
              curl -H "Authorization: Bearer ${{ secrets.CS_ACCESS_TOKEN }}"
              https://api.codescene.io/v2/projects
          - uses: leynos/shared-actions/.github/actions/generate-coverage@abc
"""


def _workflow(path: str, text: str) -> Workflow:
    """Parse an indented synthetic workflow."""
    return parse_workflow(path, textwrap.dedent(text))


def _probe(callee: str) -> tuple[Workflow, ...]:
    """Return a pull-request caller and the called-only probe it reaches."""
    return (
        _workflow(
            ".github/workflows/ci.yml", _PULL_REQUEST_CALLER.format(callee=callee)
        ),
        _workflow(".github/workflows/probe.yml", _CALLED_ONLY),
    )


@pytest.mark.parametrize(
    "callee",
    [
        "./.github/workflows/probe.yml",
        ".github/workflows/probe.yml",
        "$/.github/workflows/probe.yml",
    ],
)
def test_a_called_workflow_is_inside_the_pull_request_closure(callee: str) -> None:
    """Every pull-request clause sees a workflow reached only by a call.

    Before the closure, the token and host clauses shared one blind spot:
    each enumerated only workflows whose own trigger names a pull request.
    """
    found = _probe(callee)
    assert [str(w) for w in pull_request_closure(found)] == [
        ".github/workflows/ci.yml",
        ".github/workflows/probe.yml",
    ]
    assert token_references_for_pull_requests(found) == [".github/workflows/probe.yml"]
    assert host_references_for_pull_requests(found) == [".github/workflows/probe.yml"]
    assert reports_published_for_pull_requests(found) == [
        ".github/workflows/probe.yml: None"
    ]


def test_an_uncalled_workflow_is_outside_the_pull_request_closure() -> None:
    """The closure follows calls; it does not sweep in every workflow."""
    caller = _workflow(".github/workflows/ci.yml", "on: pull_request\njobs: {}\n")
    probe = _workflow(".github/workflows/probe.yml", _CALLED_ONLY)
    assert [str(w) for w in pull_request_closure((caller, probe))] == [
        ".github/workflows/ci.yml"
    ]


def test_a_call_to_a_missing_local_workflow_is_refused() -> None:
    """A closure that stopped at a dangling call would pass in silence."""
    with pytest.raises(LookupError, match="does not exist"):
        pull_request_closure(_probe("./.github/workflows/absent.yml")[:1])


_FORWARDING_CALLER = """
    on: pull_request
    jobs:
      call:
        uses: leynos/elsewhere/.github/workflows/x.yml@abc
        secrets:
          {forwarding}
"""


@pytest.mark.parametrize(
    "forwarding",
    [
        # The credential forwarded under another name.
        "token: ${{ secrets.CS_ACCESS_TOKEN }}",
        # Another secret forwarded under the credential's name.
        "CS_ACCESS_TOKEN: ${{ secrets.CODESCENE }}",
    ],
)
def test_forwarding_the_credential_is_a_reference(forwarding: str) -> None:
    """`secrets:` forwarding hands the credential over, by key or by value."""
    caller = _workflow(
        ".github/workflows/ci.yml", _FORWARDING_CALLER.format(forwarding=forwarding)
    )
    assert token_references_for_pull_requests((caller,)) == [str(caller)]


@pytest.mark.parametrize(
    ("uses", "expected"),
    [
        ("./.github/workflows/x.yml", ".github/workflows/x.yml"),
        (".github/workflows/x.yml", ".github/workflows/x.yml"),
        ("leynos/shared-actions/.github/workflows/x.yml@abc", None),
        ("./.github/actions/setup", None),
        ("$/.github/workflows/x.yml", ".github/workflows/x.yml"),
    ],
)
def test_local_calls_are_matched_by_shape(uses: str, expected: str | None) -> None:
    """A call is local when it names a path under the workflow directory."""
    assert local_callee(uses) == expected


@pytest.mark.parametrize(
    "condition",
    [
        "env.CS_ACCESS_TOKEN != '' && github.ref == 'refs/heads/main'",
        "${{ github.ref == 'refs/heads/main' && env.CS_ACCESS_TOKEN != '' }}",
        "env.CS_ACCESS_TOKEN != ''  &&  'refs/heads/main' == github.ref",
    ],
)
def test_a_conjunctive_guard_is_accepted(condition: str) -> None:
    """Both requirements as whole conjuncts, in either order, is a guard."""
    assert is_guarded_upload(condition)


@pytest.mark.parametrize(
    "condition",
    [
        # The sweep's named mutation: the disjunct makes both conjuncts
        # optional, while a substring test still finds the main ref.
        (
            "env.CS_ACCESS_TOKEN != '' && github.ref == 'refs/heads/main'"
            " || github.event_name == 'workflow_dispatch'"
        ),
        # The same disjunct landing on the credential's conjunct, which a
        # reader matching the ref conjunct exactly would still accept.
        (
            "github.ref == 'refs/heads/main' && env.CS_ACCESS_TOKEN != ''"
            " || github.event_name == 'workflow_dispatch'"
        ),
        # A disjunct after a third conjunct leaves both guards whole, yet
        # `||` binds loosest, so the dispatch alone uploads.
        (
            "env.CS_ACCESS_TOKEN != '' && github.ref == 'refs/heads/main'"
            " && github.actor != 'bot' || github.event_name == 'workflow_dispatch'"
        ),
        "env.CS_ACCESS_TOKEN != ''",
        "github.ref == 'refs/heads/main'",
        "env.CS_ACCESS_TOKEN == '' && github.ref == 'refs/heads/main'",
        "env.CS_ACCESS_TOKEN != '' && github.ref != 'refs/heads/main'",
        "env.CS_ACCESS_TOKEN != '' && github.ref == 'refs/heads/main-backup'",
        "env.CS_ACCESS_TOKEN != '' && (github.ref == 'refs/heads/main')",
        "",
    ],
)
def test_an_insufficient_guard_is_refused(condition: str) -> None:
    """Anything but the two whole conjuncts joined by `&&` is refused."""
    assert not is_guarded_upload(condition)


def test_a_quoted_operator_does_not_split_the_guard() -> None:
    """An operator inside a string literal is text, not logic."""
    condition = (
        "env.CS_ACCESS_TOKEN != '' && github.ref == 'refs/heads/main'"
        " && github.actor != 'a || b'"
    )
    assert is_guarded_upload(condition)


@pytest.mark.parametrize(
    ("document", "expected"),
    [
        ({"concurrency": {"group": "g", "cancel-in-progress": True}}, ["workflow"]),
        ({"concurrency": {"group": "g", "cancel-in-progress": "true"}}, ["workflow"]),
        (
            {"concurrency": {"group": "g", "cancel-in-progress": "${{ true }}"}},
            ["workflow"],
        ),
        (
            {"jobs": {"upload": {"concurrency": {"cancel-in-progress": True}}}},
            ["job 'upload'"],
        ),
        ({"concurrency": {"group": "g", "cancel-in-progress": False}}, []),
        ({"concurrency": {"group": "g"}}, []),
        ({"concurrency": "g"}, []),
    ],
)
def test_cancelling_concurrency_is_seen_at_every_scope(
    document: dict[object, object], expected: list[str]
) -> None:
    """Only an absent value or a literal false queues."""
    assert cancelling_scopes(Workflow("w.yml", document)) == expected


@pytest.mark.parametrize(
    "document",
    [
        {"on": {"pull_request": None}},
        {True: {"pull_request": None}},
        {"on": "pull_request"},
        {"on": ["push", "pull_request"]},
        {True: ["push", "pull_request_target"]},
    ],
)
def test_every_trigger_form_is_read(document: dict[object, object]) -> None:
    """Scalar, sequence and mapping forms, under either key, are all read.

    A mapping-only reader turns `on: [push, pull_request]` into one key
    naming both, and the workflow escapes every pull-request clause.
    """
    assert serves_pull_requests(document)


def test_an_unrecognized_trigger_form_is_refused() -> None:
    """A form the reader cannot interpret is not read as having no triggers."""
    with pytest.raises(TypeError, match="unrecognized trigger form"):
        triggers({"on": 1})


def test_an_unquoted_trigger_key_is_still_read() -> None:
    """YAML 1.1 reads an unquoted `on:` as the boolean true."""
    quoted: dict[object, object] = {"on": {"push": {"branches": ["main"]}}}
    unquoted: dict[object, object] = {True: {"push": {"branches": ["main"]}}}
    assert triggers(quoted) == triggers(unquoted)
    assert pushes_to_main(unquoted)
    assert not serves_pull_requests(unquoted)


def test_a_publisher_must_not_also_serve_pull_requests() -> None:
    """The publisher predicate reads both halves, not just the push trigger."""
    both: dict[object, object] = {
        "on": {"push": {"branches": ["main"]}, "pull_request": None}
    }
    assert pushes_to_main(both)
    assert serves_pull_requests(both)
    assert not pushes_to_main({"on": {"push": {"branches": ["release"]}}})
    assert not is_trunk_publisher(both)
    assert is_trunk_publisher({"on": {"push": {"branches": ["main"]}}})


def test_a_push_to_main_among_other_branches_is_not_a_trunk_push() -> None:
    """A filter naming another branch too would publish that branch as main."""
    assert not pushes_to_main({"on": {"push": {"branches": ["main", "release"]}}})
    assert not pushes_to_main({"on": {"push": None}})
    assert pushes_to_main({"on": {"push": {"branches": "main"}}})


def test_an_omitted_artefact_input_counts_as_publishing() -> None:
    """The effective value is read, not the spelling."""
    assert publishes_report({"with": {}}), "an omitted input takes the default"
    assert publishes_report({}), "a step with no inputs takes it too"
    assert publishes_report({"with": {"publish-artefact": "true"}})
    assert not publishes_report({"with": {"publish-artefact": "false"}})
    assert not publishes_report({"with": {"publish-artefact": False}})


def test_a_duplicate_key_is_refused_at_load() -> None:
    """A key declared twice must not silently keep its last value."""
    with pytest.raises(DuplicateKeyError):
        _workflow(
            "w.yml",
            """
            jobs:
              a:
                runs-on: ubuntu-latest
                runs-on: ubuntu-latest
            """,
        )


_UPLOADING_CALLEE = """
    on:
      workflow_call:
    jobs:
      upload:
        runs-on: ubuntu-latest
        steps:
          - uses: leynos/shared-actions/.github/actions/upload-codescene-coverage@abc
"""


def test_an_uploader_two_calls_away_is_found() -> None:
    """The closure is transitive, and the uploader clause reads all of it."""
    found = (
        _workflow(
            ".github/workflows/ci.yml",
            _PULL_REQUEST_CALLER.format(callee="./.github/workflows/middle.yml"),
        ),
        _workflow(
            ".github/workflows/middle.yml",
            """
            on:
              workflow_call:
            jobs:
              call:
                uses: ./.github/workflows/upload.yml
            """,
        ),
        _workflow(".github/workflows/upload.yml", _UPLOADING_CALLEE),
    )
    assert uploads_for_pull_requests(found) == [".github/workflows/upload.yml"]
    assert uploads_for_pull_requests(found[1:]) == [], (
        "without the pull-request caller nothing reaches the uploader"
    )


@pytest.mark.parametrize(
    ("secrets", "expected"),
    [("secrets: inherit", True), ("", False)],
)
def test_inheriting_into_a_remote_workflow_hands_over_the_credential(
    secrets: str, *, expected: bool
) -> None:
    """A remote callee cannot be read, so inheriting into one is refused.

    The same call without forwarded secrets is the control.
    """
    caller = _workflow(
        ".github/workflows/ci.yml",
        _FORWARDING_CALLER.replace("secrets:\n          {forwarding}", secrets),
    )
    assert (token_references_for_pull_requests((caller,)) == [str(caller)]) is expected


@pytest.mark.parametrize(
    ("concurrency", "expected"),
    [
        ({"group": "coverage-main-${{ github.ref }}"}, []),
        ({"group": "${{ github.workflow }}-${{github.ref_name}}"}, []),
        ("coverage-main", []),
        ({"group": "coverage-${{ github.run_id }}"}, ["github.run_id"]),
        ({"group": "coverage-${{ github.sha }}"}, ["github.sha"]),
        (
            {"group": "coverage-${{ github.event_name }}-${{ github.ref }}"},
            ["github.event_name"],
        ),
        (
            {"group": "${{ github.head_ref || github.run_id }}"},
            ["github.head_ref || github.run_id"],
        ),
    ],
)
def test_a_group_that_varies_per_run_is_seen(
    concurrency: object, expected: list[str]
) -> None:
    """Runs in different groups do not wait for each other."""
    assert unstable_group_expressions(concurrency) == expected


@pytest.mark.parametrize(
    "uses", ["$/.github/workflows/x.yml@main", "./.github/workflows/x.yml@v1"]
)
def test_a_local_call_with_a_ref_is_refused(uses: str) -> None:
    """A local path carrying a ref is neither local nor a remote call to skip."""
    with pytest.raises(ValueError, match="carries a ref"):
        local_callee(uses)


def test_a_workflow_declaring_both_trigger_keys_is_refused() -> None:
    """GitHub merges both spellings, so reading one would hide the other."""
    with pytest.raises(TypeError, match="both"):
        triggers({"on": {"push": None}, True: {"pull_request": None}})


def test_the_host_is_found_in_any_case_and_any_scope() -> None:
    """A workflow-level shell default reaches CodeScene as surely as a step."""
    found = (
        _workflow(
            ".github/workflows/ci.yml",
            """
            on: pull_request
            defaults:
              run:
                shell: curl -s https://API.CodeScene.IO/v2 ; bash {0}
            jobs: {}
            """,
        ),
    )
    assert host_references_for_pull_requests(found) == [".github/workflows/ci.yml"]


_UPLOAD_STEP = {
    "name": "Upload",
    "uses": "leynos/shared-actions/.github/actions/upload-codescene-coverage@abc",
    "env": {"CS_ACCESS_TOKEN": "${{ secrets.CS_ACCESS_TOKEN }}"},
    "with": {"access-token": "${{env.CS_ACCESS_TOKEN}}"},
}


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({}, {}),
        ({"env": {}}, {"Upload": "env.CS_ACCESS_TOKEN bound to the secret"}),
        (
            {"env": {"CS_ACCESS_TOKEN": "${{ secrets.OTHER }}"}},
            {"Upload": "env.CS_ACCESS_TOKEN bound to the secret"},
        ),
        ({"with": {}}, {"Upload": "access-token passing it on"}),
    ],
)
def test_the_upload_step_must_bind_and_pass_the_credential(
    changes: dict[str, object], expected: dict[str, str]
) -> None:
    """Deleting the binding leaves the guard true-looking and the upload dead."""
    step = _UPLOAD_STEP | changes
    publisher = Workflow("w.yml", {"jobs": {"upload": {"steps": [step]}}})
    assert unbound_uploads(publisher) == expected, step
