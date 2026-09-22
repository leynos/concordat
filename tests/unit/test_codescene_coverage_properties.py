"""Independent property tests for CV-005 main-owned CodeScene coverage.

Each generated case is a set of semantic decisions about a repository's
coverage topology: which trigger spellings it uses, where its token sits,
which platform ratchets, whether the publisher is guarded. The case is
rendered into workflow documents and evaluated by the real Conftest policy,
then compared with expectations computed directly from the decisions. The
expectations are deliberately written as independent predicates over the
case rather than as a second reading of the rendered document, so a defect
in the policy's document reader cannot be mirrored here.

Shell-command grammar and malformed-YAML handling stay in the Rego fixture
suite: generating them here would only exercise two copies of one parser.
"""

from __future__ import annotations

import dataclasses
import typing as typ

from hypothesis import given, settings
from hypothesis import strategies as st

from concordat.rules import runner

_RULE_ID: typ.Final = "main-owned-codescene-coverage"
_KIND: typ.Final = "policy-input/main-owned-codescene-coverage"
_GENERATOR: typ.Final = (
    "leynos/shared-actions/.github/actions/generate-coverage@0000000"
)
_UPLOADER: typ.Final = (
    "leynos/shared-actions/.github/actions/upload-codescene-coverage@0000000"
)
_PR_PATH: typ.Final = ".github/workflows/ci.yml"
_MAIN_PATH: typ.Final = ".github/workflows/coverage-main.yml"
_CREDENTIAL: typ.Final = "${{ secrets.CS_ACCESS_TOKEN }}"

_REF_GUARD_MESSAGE: typ.Final = (
    "CodeScene upload step is not guarded on github.ref == 'refs/heads/main'"
)
_CREDENTIAL_GUARD_MESSAGE: typ.Final = (
    "CodeScene upload step is not guarded on the CS_ACCESS_TOKEN credential"
)

_PLATFORM_LABELS: typ.Final = {
    "Linux": "ubuntu-latest",
    "Windows": "windows-latest",
    "macOS": "macos-14",
}


@dataclasses.dataclass(frozen=True)
class CoverageCase:
    """One repository's coverage topology, stated as decisions not syntax."""

    trigger_key: str
    pr_trigger_form: str
    ratchet_value: bool | str
    publish_artefact: bool | str | None
    pr_platform: str
    trunk_runs_pr_platform: bool
    publisher_dispatch: bool
    publisher_ref_guard: bool
    publisher_token_guard: bool
    publisher_concurrency: bool
    pr_invokes_codescene: bool
    pr_credential_site: str


_CASES = st.builds(
    CoverageCase,
    trigger_key=st.sampled_from(["on", "true"]),
    pr_trigger_form=st.sampled_from(["scalar", "sequence", "mapping"]),
    ratchet_value=st.sampled_from([True, "true", False]),
    publish_artefact=st.sampled_from([None, False, "false", True, "true"]),
    pr_platform=st.sampled_from(["Linux", "Windows", "macOS"]),
    trunk_runs_pr_platform=st.booleans(),
    publisher_dispatch=st.booleans(),
    publisher_ref_guard=st.booleans(),
    publisher_token_guard=st.booleans(),
    publisher_concurrency=st.booleans(),
    pr_invokes_codescene=st.booleans(),
    pr_credential_site=st.sampled_from(["none", "workflow", "job", "step"]),
)


def _is_falsey(value: object) -> bool:
    """Return whether an Actions input spells a false value."""
    return value is False or (isinstance(value, str) and value.lower() == "false")


def _is_ratcheting(value: object) -> bool:
    """Return whether an Actions input spells the ratchet's true value."""
    return value is True or value == "true"


def _pr_triggers(case: CoverageCase) -> object:
    """Render the pull-request trigger in one of its three shapes."""
    if case.pr_trigger_form == "scalar":
        return "pull_request"
    if case.pr_trigger_form == "sequence":
        return ["pull_request"]
    return {"pull_request": None}


def _coverage_step(
    *, ratchet: object, publish: object | None, suffix: str = ""
) -> dict[str, object]:
    """Build one generate-coverage step with the generated input values."""
    inputs: dict[str, object] = {"with-ratchet": ratchet}
    if publish is not None:
        inputs["publish-artefact"] = publish
    if suffix:
        inputs["artefact-name-suffix"] = suffix
    return {"uses": _GENERATOR, "with": inputs}


def _pr_workflow(case: CoverageCase) -> dict[str, object]:
    """Render the pull-request workflow described by one case."""
    step = _coverage_step(ratchet=case.ratchet_value, publish=case.publish_artefact)
    if case.pr_credential_site == "step":
        step["env"] = {"CS_ACCESS_TOKEN": _CREDENTIAL}
    steps: list[dict[str, object]] = [step]
    if case.pr_invokes_codescene:
        steps.append({"uses": _UPLOADER, "with": {"mode": "check"}})
    job: dict[str, object] = {
        "runs-on": _PLATFORM_LABELS[case.pr_platform],
        "steps": steps,
    }
    if case.pr_credential_site == "job":
        job["env"] = {"CS_ACCESS_TOKEN": _CREDENTIAL}
    parsed: dict[str, object] = {case.trigger_key: _pr_triggers(case)}
    if case.pr_credential_site == "workflow":
        parsed["env"] = {"CS_ACCESS_TOKEN": _CREDENTIAL}
    parsed["jobs"] = {"coverage": job}
    return {"path": _PR_PATH, "parsed": parsed, "error": None}


def _upload_condition(case: CoverageCase) -> str | None:
    """Render the upload step's guard from the generated decisions."""
    clauses = []
    if case.publisher_ref_guard:
        clauses.append("github.ref == 'refs/heads/main'")
    if case.publisher_token_guard:
        clauses.append("env.CS_ACCESS_TOKEN != ''")
    if not clauses:
        return None
    return "${{ " + " && ".join(clauses) + " }}"


def _main_workflow(case: CoverageCase) -> dict[str, object]:
    """Render the push-to-main publisher described by one case."""
    upload: dict[str, object] = {"uses": _UPLOADER}
    condition = _upload_condition(case)
    if condition is not None:
        upload["if"] = condition
    upload["with"] = {"mode": "upload", "access-token": "${{ env.CS_ACCESS_TOKEN }}"}
    jobs: dict[str, object] = {
        "coverage-upload": {
            "runs-on": _PLATFORM_LABELS["Linux"],
            "steps": [_coverage_step(ratchet=True, publish=None), upload],
        }
    }
    if case.trunk_runs_pr_platform and case.pr_platform != "Linux":
        jobs["coverage-platform"] = {
            "runs-on": _PLATFORM_LABELS[case.pr_platform],
            "steps": [_coverage_step(ratchet=True, publish=None)],
        }
    triggers: dict[str, object] = {"push": {"branches": ["main"]}}
    if case.publisher_dispatch:
        triggers["workflow_dispatch"] = None
    parsed: dict[str, object] = {case.trigger_key: triggers}
    if case.publisher_concurrency:
        parsed["concurrency"] = {
            "group": "coverage-main-${{ github.ref }}",
            "cancel-in-progress": False,
        }
    parsed["jobs"] = jobs
    return {"path": _MAIN_PATH, "parsed": parsed, "error": None}


def _envelope(case: CoverageCase) -> dict[str, object]:
    """Assemble the policy input for one generated case."""
    return {
        "schema_version": 1,
        "kind": _KIND,
        "repository": {"path": "/checkout", "name": None},
        "workflows": [_pr_workflow(case), _main_workflow(case)],
    }


def _trunk_platforms(case: CoverageCase) -> set[str]:
    """Return the platforms the generated trunk push ratchets on."""
    platforms = {"Linux"}
    if case.trunk_runs_pr_platform:
        platforms.add(case.pr_platform)
    return platforms


def _platform_expectation(case: CoverageCase) -> tuple[bool, str, str]:
    """Return the platform clause's predicate, path and message."""
    platform = case.pr_platform
    unmatched = _is_ratcheting(case.ratchet_value) and (
        platform not in _trunk_platforms(case)
    )
    message = (
        f"pull requests ratchet coverage on {platform} "
        f"with no {platform} lane on the trunk push"
    )
    return unmatched, ".github/workflows", message


def _expected(case: CoverageCase) -> set[tuple[str, str, str]]:
    """Compute the findings one case must produce, from its decisions alone.

    Each entry is an independent predicate over the generated decisions,
    stated beside the message it implies. None of them reads the rendered
    workflow document, so a defect in the policy's reader cannot be mirrored
    here.

    Returns
    -------
    set[tuple[str, str, str]]
        Verdict, path and message for every clause the case violates.
    """
    clauses: list[tuple[bool, str, str]] = [
        (
            case.pr_invokes_codescene,
            _PR_PATH,
            "pull-request workflow invokes CodeScene",
        ),
        (
            case.pr_credential_site != "none",
            _PR_PATH,
            "pull-request workflow receives CS_ACCESS_TOKEN",
        ),
        (
            not _is_ratcheting(case.ratchet_value),
            _PR_PATH,
            "pull-request workflow lacks ratcheting coverage generation",
        ),
        (
            not _is_falsey(case.publish_artefact),
            _PR_PATH,
            "pull-request coverage does not set publish-artefact false",
        ),
        (not case.publisher_ref_guard, _MAIN_PATH, _REF_GUARD_MESSAGE),
        (not case.publisher_token_guard, _MAIN_PATH, _CREDENTIAL_GUARD_MESSAGE),
        (
            not case.publisher_concurrency,
            _MAIN_PATH,
            "main coverage publisher has no concurrency block",
        ),
        _platform_expectation(case),
    ]
    return {
        ("noncompliant", path, message) for applies, path, message in clauses if applies
    }


def _findings(case: CoverageCase) -> set[tuple[str, str, str]]:
    """Evaluate one case against the real policy through Conftest."""
    results = runner._invoke_conftest(_RULE_ID, typ.cast("typ.Any", _envelope(case)))
    return {
        (finding.verdict, finding.path, finding.message)
        for finding in runner._findings_from_results(results)
    }


_COMPLIANT_CASE: typ.Final = CoverageCase(
    trigger_key="on",
    pr_trigger_form="mapping",
    ratchet_value=True,
    publish_artefact="false",
    pr_platform="Linux",
    trunk_runs_pr_platform=True,
    publisher_dispatch=True,
    publisher_ref_guard=True,
    publisher_token_guard=True,
    publisher_concurrency=True,
    pr_invokes_codescene=False,
    pr_credential_site="none",
)

_NONCOMPLIANT_CASE: typ.Final = dataclasses.replace(
    _COMPLIANT_CASE,
    trigger_key="true",
    pr_trigger_form="scalar",
    ratchet_value=False,
    publish_artefact=True,
    pr_platform="Windows",
    trunk_runs_pr_platform=False,
    publisher_dispatch=False,
    publisher_ref_guard=False,
    publisher_token_guard=False,
    publisher_concurrency=False,
    pr_invokes_codescene=True,
    pr_credential_site="workflow",
)


def test_a_fully_compliant_case_produces_no_findings() -> None:
    """Anchor the generator: its compliant corner really is compliant."""
    findings = _findings(_COMPLIANT_CASE)

    assert findings == set(), findings
    assert _expected(_COMPLIANT_CASE) == set(), _expected(_COMPLIANT_CASE)


def test_a_fully_noncompliant_case_reports_every_clause() -> None:
    """Anchor the other corner: each clause can fire, and does, together."""
    findings = _findings(_NONCOMPLIANT_CASE)

    assert findings == _expected(_NONCOMPLIANT_CASE), findings
    assert len(findings) == 7, findings


@settings(max_examples=40, deadline=None)
@given(_CASES)
def test_policy_findings_match_the_generated_topology(case: CoverageCase) -> None:
    """Every generated topology yields exactly the findings its decisions imply."""
    assert _findings(case) == _expected(case), case
