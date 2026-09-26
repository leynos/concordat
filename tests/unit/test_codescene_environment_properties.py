"""Property tests for the `codescene` environment: CV-005's clause and CV-006.

Each case is a set of decisions: which jobs upload and how each declares its
environment, or where the environment and the token stand in the settings.
The case is rendered into a workflow or a settings state and judged by the
real policy or check, and the findings are compared with expectations read
from the decisions through fixed tables. The tables name outcomes per
decision rather than re-reading the rendered document, so a defect in the
reader cannot be mirrored here.
"""

from __future__ import annotations

import dataclasses
import typing as typ

from hypothesis import given, settings
from hypothesis import strategies as st

from concordat.auditor import codescene_environment as cv006
from concordat.auditor.models import AuditContext, RepositorySnapshot
from concordat.rules import runner

_RULE_ID: typ.Final = "main-owned-codescene-coverage"
_KIND: typ.Final = "policy-input/main-owned-codescene-coverage"
_UPLOADER: typ.Final = (
    "leynos/shared-actions/.github/actions/upload-codescene-coverage@0000000"
)

# How a job spells its environment, and whether GitHub reads that as the
# `codescene` environment.
_DECLARATIONS: typ.Final[dict[str, tuple[object, bool]]] = {
    "none": (None, False),
    "exact": ("codescene", True),
    "other case": ("CodeScene", True),
    "mapping": ({"name": "codescene", "url": "https://codescene.io"}, True),
    "near miss": ("codescene-prod", False),
    "expression": ("${{ 'codescene' }}", False),
    "unrelated": ("preview", False),
}


@dataclasses.dataclass(frozen=True)
class JobCase:
    """One job: whether it uploads and how it declares its environment."""

    uploads: bool
    declaration: str


@dataclasses.dataclass(frozen=True)
class WorkflowCase:
    """One workflow: its trigger and its jobs."""

    pull_request: bool
    jobs: tuple[JobCase, ...]


_WORKFLOWS = st.builds(
    WorkflowCase,
    pull_request=st.booleans(),
    jobs=st.lists(
        st.builds(
            JobCase,
            uploads=st.booleans(),
            declaration=st.sampled_from(sorted(_DECLARATIONS)),
        ),
        min_size=1,
        max_size=4,
    ).map(tuple),
)


def _render(case: WorkflowCase) -> dict[str, object]:
    """Render one workflow case as a decoded workflow fact."""
    jobs: dict[str, object] = {}
    for index, job in enumerate(case.jobs):
        step: dict[str, object] = (
            {"uses": _UPLOADER, "with": {"mode": "upload"}}
            if job.uploads
            else {"run": "make lint"}
        )
        rendered: dict[str, object] = {"runs-on": "ubuntu-latest", "steps": [step]}
        spelling = _DECLARATIONS[job.declaration][0]
        if spelling is not None:
            rendered["environment"] = spelling
        jobs[f"job{index}"] = rendered
    trigger: object = (
        {"pull_request": None}
        if case.pull_request
        else {"push": {"branches": ["main"]}}
    )
    return {
        "path": ".github/workflows/w.yml",
        "parsed": {"on": trigger, "jobs": jobs},
        "error": None,
    }


def _expected(case: WorkflowCase) -> set[str]:
    """Decide the environment clause's findings from the decisions alone."""
    expected: set[str] = set()
    for index, job in enumerate(case.jobs):
        declares = _DECLARATIONS[job.declaration][1]
        name = f'"job{index}"'
        if case.pull_request:
            if declares:
                expected.add(
                    f"pull-request workflow job {name} declares environment codescene"
                )
        elif job.uploads and not declares:
            expected.add(
                f"CodeScene upload job {name} does not declare environment: codescene"
            )
        elif declares and not job.uploads:
            expected.add(
                f"job {name} declares environment codescene but holds no CodeScene "
                "upload step"
            )
    return expected


def _environment_findings(case: WorkflowCase) -> set[str]:
    """Run the real policy and keep the environment clause's messages."""
    envelope = {
        "schema_version": 1,
        "kind": _KIND,
        "repository": {"path": "/checkout", "name": None},
        "workflows": [_render(case)],
    }
    results = runner._invoke_conftest(_RULE_ID, typ.cast("typ.Any", envelope))
    return {
        finding.message
        for finding in runner._findings_from_results(results)
        if "environment" in finding.message
    }


@settings(max_examples=60, deadline=None)
@given(case=_WORKFLOWS)
def test_the_environment_clause_matches_the_generated_jobs(case: WorkflowCase) -> None:
    """Every generated workflow yields exactly the findings its jobs imply."""
    assert _environment_findings(case) == _expected(case), case


# Where the environment stands, as the settings fields it implies and the
# policy statuses it earns once the environment exists.
_POLICIES: typ.Final[dict[str, tuple[dict[str, object], list[str]]]] = {
    "main only": (
        {"custom_branch_policies": True, "branch_policies": (("main", "branch"),)},
        [],
    ),
    "shortcut": (
        {"protected_branches": True, "custom_branch_policies": False},
        ["policy-not-custom"],
    ),
    "no policy": ({"custom_branch_policies": False}, ["policy-not-custom"]),
    "main and release": (
        {
            "custom_branch_policies": True,
            "branch_policies": (("main", "branch"), ("release/*", "branch")),
        },
        ["policy-not-main-only"],
    ),
    "every branch": (
        {"custom_branch_policies": True, "branch_policies": (("*", "branch"),)},
        ["policy-not-main-only"],
    ),
    "tag named main": (
        {"custom_branch_policies": True, "branch_policies": (("main", "tag"),)},
        ["policy-not-main-only"],
    ),
}
# Where the token sits, as the secret listings and the status it earns.
_TOKEN_SITES: typ.Final[
    dict[str, tuple[tuple[str, ...], tuple[str, ...], list[str]]]
] = {
    "environment": (("CS_ACCESS_TOKEN",), ("OTHER",), []),
    "repository": ((), ("CS_ACCESS_TOKEN",), ["secret-not-moved"]),
    "both": (("CS_ACCESS_TOKEN",), ("CS_ACCESS_TOKEN",), ["secret-not-moved"]),
    "neither": (("OTHER",), (), ["secret-missing"]),
}


@dataclasses.dataclass(frozen=True)
class SettingsCase:
    """One repository's CodeScene settings, stated as decisions."""

    uploads: bool
    environment_exists: bool
    policy: str
    token_site: str
    refused: bool


_SETTINGS = st.builds(
    SettingsCase,
    uploads=st.booleans(),
    environment_exists=st.booleans(),
    policy=st.sampled_from(sorted(_POLICIES)),
    token_site=st.sampled_from(sorted(_TOKEN_SITES)),
    refused=st.booleans(),
)


def _state(case: SettingsCase) -> cv006.CodesceneCredentials:
    """Render one settings case as the state `fetch` would return."""
    environment_secrets, repository_secrets, _ = _TOKEN_SITES[case.token_site]
    fields: dict[str, object] = {
        "uploads": case.uploads,
        "repository_secrets": repository_secrets,
        "refused": ("environment secrets",) if case.refused else (),
    }
    if case.environment_exists:
        fields |= {
            "environment_exists": True,
            "environment_secrets": environment_secrets,
        }
        fields |= _POLICIES[case.policy][0]
    return cv006.CodesceneCredentials(**typ.cast("dict[str, typ.Any]", fields))


def _expected_statuses(case: SettingsCase) -> list[str]:
    """Decide CV-006's statuses from the decisions alone."""
    if not case.uploads:
        return []
    if case.refused:
        return ["indeterminate"]
    if not case.environment_exists:
        return ["environment-missing"]
    return _POLICIES[case.policy][1] + _TOKEN_SITES[case.token_site][2]


@given(case=_SETTINGS)
def test_cv006_matches_the_generated_settings(case: SettingsCase) -> None:
    """Every generated settings state yields exactly the statuses it implies."""
    context = AuditContext(
        repository=RepositorySnapshot(
            owner="o",
            name="r",
            default_branch="main",
            allow_squash_merge=True,
            allow_merge_commit=False,
            allow_rebase_merge=False,
            allow_auto_merge=False,
            delete_branch_on_merge=True,
        ),
        branch_protection=None,
        teams=(),
        collaborators=(),
        labels=(),
        priority_model=None,
        codescene=_state(case),
    )
    statuses = [
        typ.cast("dict[str, str]", finding.properties)["status"]
        for finding in cv006.run(context)
    ]
    assert statuses == _expected_statuses(case), case
