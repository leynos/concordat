"""Property tests for the `codescene` environment settings audit (CV-006).

Each case is a set of decisions: where the environment and the token stand in
the settings. The case is rendered into a settings state and judged by the real
check, and the findings are compared with expectations read from the decisions
through fixed tables. The tables name outcomes per decision rather than
re-reading the rendered state, so a defect in the reader cannot be mirrored
here.
"""

from __future__ import annotations

import dataclasses
import typing as typ

from hypothesis import given
from hypothesis import strategies as st

from concordat.auditor import codescene_environment as cv006
from concordat.auditor.models import AuditContext, RepositorySnapshot

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
