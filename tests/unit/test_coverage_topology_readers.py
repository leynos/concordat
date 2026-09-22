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
    local_callee,
    parse_workflow,
    publishes_report,
    pull_request_closure,
    pushes_to_main,
    reports_published_for_pull_requests,
    serves_pull_requests,
    token_references_for_pull_requests,
    triggers,
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
    "callee", ["./.github/workflows/probe.yml", ".github/workflows/probe.yml"]
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
