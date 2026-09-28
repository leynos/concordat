"""Unit tests for CV-006, the CodeScene token's main-only environment."""

from __future__ import annotations

import dataclasses
import typing as typ

import pytest

from concordat.auditor import codescene_environment as cv006
from concordat.auditor.github import (
    GithubClient,
    GithubError,
    GithubForbiddenError,
    GithubNotFoundError,
)
from concordat.auditor.models import AuditContext, RepositorySnapshot

_REPOSITORY = RepositorySnapshot(
    owner="example",
    name="demo",
    default_branch="main",
    allow_squash_merge=True,
    allow_merge_commit=False,
    allow_rebase_merge=False,
    allow_auto_merge=False,
    delete_branch_on_merge=True,
)
_READY = cv006.CodesceneCredentials(
    uploads=True,
    environment_exists=True,
    protected_branches=False,
    custom_branch_policies=True,
    branch_policies=(("main", "branch"),),
    environment_secrets=("CS_ACCESS_TOKEN",),
    repository_secrets=(),
)


def _statuses(state: cv006.CodesceneCredentials | None) -> list[str]:
    """Run CV-006 over one state and return each finding's status."""
    context = AuditContext(
        repository=_REPOSITORY,
        branch_protection=None,
        teams=(),
        collaborators=(),
        labels=(),
        priority_model=None,
        codescene=state,
    )
    findings = cv006.run(context)
    assert all(finding.rule_id == "CV-006" for finding in findings), findings
    return [typ.cast("dict[str, str]", f.properties)["status"] for f in findings]


def test_the_main_only_home_is_compliant() -> None:
    """Environment, main-only custom policy, token there and nowhere else."""
    assert _statuses(_READY) == []


@pytest.mark.parametrize(
    "state",
    [None, cv006.CodesceneCredentials(uploads=False)],
    ids=["no settings read", "uploads nothing"],
)
def test_a_repository_that_uploads_nothing_is_not_a_subject(
    state: cv006.CodesceneCredentials | None,
) -> None:
    """Only a repository whose workflows upload to CodeScene is judged."""
    assert _statuses(state) == []


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        pytest.param(
            {"environment_exists": False, "repository_secrets": ("CS_ACCESS_TOKEN",)},
            ["environment-missing"],
            id="environment missing, token still a repository secret",
        ),
        pytest.param(
            {"repository_secrets": ("CS_ACCESS_TOKEN",)},
            ["secret-not-moved"],
            id="environment ready, secret not yet moved",
        ),
        pytest.param(
            {"repository_secrets": ("CS_ACCESS_TOKEN",), "environment_secrets": ()},
            ["secret-not-moved"],
            id="environment ready and empty, secret not yet moved",
        ),
        pytest.param(
            {"environment_secrets": ()},
            ["secret-missing"],
            id="token nowhere",
        ),
        pytest.param(
            {"protected_branches": True, "custom_branch_policies": False},
            ["policy-not-custom"],
            id="protected-branches shortcut",
        ),
        pytest.param(
            {"custom_branch_policies": False, "branch_policies": ()},
            ["policy-not-custom"],
            id="no branch policy",
        ),
        pytest.param(
            {"branch_policies": (("main", "branch"), ("release/*", "branch"))},
            ["policy-not-main-only"],
            id="a second branch admitted",
        ),
        pytest.param(
            {"branch_policies": (("*", "branch"),)},
            ["policy-not-main-only"],
            id="every branch admitted",
        ),
        pytest.param(
            {"branch_policies": (("main", "tag"),)},
            ["policy-not-main-only"],
            id="a tag named main",
        ),
        pytest.param(
            {"branch_policies": ()},
            ["policy-not-main-only"],
            id="custom policy admitting nothing",
        ),
        pytest.param(
            {"protected_branches": True, "repository_secrets": ("CS_ACCESS_TOKEN",)},
            ["policy-not-custom", "secret-not-moved"],
            id="policy and secret both outstanding",
        ),
    ],
)
def test_each_gap_is_reported_by_its_own_status(
    changes: dict[str, object], expected: list[str]
) -> None:
    """A rollout reads as progress: each remaining step has its own status.

    A missing environment reports that alone, so the secret still being a
    repository secret is not a second finding until the environment exists.
    """
    state = dataclasses.replace(_READY, **typ.cast("dict[str, typ.Any]", changes))
    assert _statuses(state) == expected, state


def test_a_refused_read_is_indeterminate_not_a_pass() -> None:
    """A token that may not read secrets cannot clear the check."""
    state = dataclasses.replace(_READY, refused=("environment secrets",))
    assert _statuses(state) == ["indeterminate"]


class _FakeClient:
    """Answer the CV-006 reads from fixed values, raising where configured."""

    def __init__(self, **answers: object) -> None:
        """Answer each read from *answers*, keyed by method name."""
        self.answers = answers
        self.calls: list[str] = []

    def _answer(self, name: str) -> object:
        """Record one read and return or raise its configured answer."""
        self.calls.append(name)
        answer = self.answers.get(name)
        if isinstance(answer, Exception):
            raise answer
        return answer

    def workflow_texts(self, _owner: str, _name: str) -> object:
        """Answer the workflow read."""
        return self._answer("workflow_texts")

    def repository_secret_names(self, _owner: str, _name: str) -> object:
        """Answer the repository secret read."""
        return self._answer("repository_secret_names")

    def environment(self, _owner: str, _name: str, _environment: str) -> object:
        """Answer the environment read."""
        return self._answer("environment")

    def environment_branch_policies(
        self, _owner: str, _name: str, _environment: str
    ) -> object:
        """Answer the branch policy read."""
        return self._answer("environment_branch_policies")

    def environment_secret_names(
        self, _owner: str, _name: str, _environment: str
    ) -> object:
        """Answer the environment secret read."""
        return self._answer("environment_secret_names")


_UPLOADING = ("jobs:\n  u:\n    steps:\n      - uses: x/upload-codescene-coverage@y\n",)
_READY_ANSWERS: dict[str, object] = {
    "workflow_texts": _UPLOADING,
    "repository_secret_names": ("OTHER",),
    "environment": {
        "deployment_branch_policy": {
            "protected_branches": False,
            "custom_branch_policies": True,
        }
    },
    "environment_branch_policies": (("main", "branch"),),
    "environment_secret_names": ("CS_ACCESS_TOKEN",),
}


def _fetch(**overrides: object) -> cv006.CodesceneCredentials:
    """Fetch through a fake client answering the ready repository's settings."""
    client = _FakeClient(**(_READY_ANSWERS | overrides))
    return cv006.fetch(typ.cast("GithubClient", client), "example", "demo")


def test_fetch_reads_a_ready_repository() -> None:
    """The fetched state of a ready repository passes the check."""
    state = _fetch()
    assert state == dataclasses.replace(_READY, repository_secrets=("OTHER",))
    assert _statuses(state) == []


def test_fetch_stops_at_a_repository_that_uploads_nothing() -> None:
    """No uploader, no subject: the settings are not read at all."""
    client = _FakeClient(workflow_texts=("jobs: {}\n",))
    state = cv006.fetch(typ.cast("GithubClient", client), "example", "demo")
    assert state == cv006.CodesceneCredentials(uploads=False)
    assert client.calls == ["workflow_texts"], client.calls


def test_fetch_reports_a_missing_environment() -> None:
    """A 404 on the environment is its absence, not a refusal."""
    state = _fetch(environment=None, repository_secret_names=("CS_ACCESS_TOKEN",))
    assert _statuses(state) == ["environment-missing"]


def test_fetch_skips_custom_policies_under_the_shortcut() -> None:
    """The protected-branches shortcut has no custom policies to list."""
    state = _fetch(
        environment={
            "deployment_branch_policy": {
                "protected_branches": True,
                "custom_branch_policies": False,
            }
        },
        environment_branch_policies=AssertionError("not read"),
    )
    assert _statuses(state) == ["policy-not-custom"]


@pytest.mark.parametrize(
    "refused",
    [
        "workflow_texts",
        "repository_secret_names",
        "environment",
        "environment_branch_policies",
        "environment_secret_names",
    ],
)
def test_fetch_records_every_refused_read(refused: str) -> None:
    """Any refused read makes the result indeterminate, never a pass."""
    state = _fetch(**{refused: GithubForbiddenError("403")})
    assert state.refused, state
    assert _statuses(state) == ["indeterminate"]


@dataclasses.dataclass
class _Response:
    """A stand-in for the one `requests.Response` field the client reads."""

    status_code: int
    text: str = ""
    headers: dict[str, str] = dataclasses.field(default_factory=dict)


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (401, GithubForbiddenError),
        (403, GithubForbiddenError),
        (404, GithubNotFoundError),
    ],
)
def test_the_client_names_refusal_and_absence_apart(
    monkeypatch: pytest.MonkeyPatch, status: int, error: type[Exception]
) -> None:
    """A 401 or 403 is a refusal and a 404 an absence; neither is a pass."""
    client = GithubClient(token="t")  # noqa: S106 - a placeholder, never sent
    monkeypatch.setattr(
        client.session, "request", lambda *_args, **_kwargs: _Response(status)
    )
    with pytest.raises(error):
        client.repository_secret_names("example", "demo")


_ACTION = "leynos/shared-actions/.github/actions/upload-codescene-coverage@abc"


def _workflow(*step_lines: str) -> str:
    """Render one job whose steps are the given YAML lines."""
    body = "".join(f"      {line}\n" for line in step_lines)
    return f"jobs:\n  u:\n    steps:\n{body}"


def _action(mode: str | None = None) -> str:
    """Render one upload action step, with a `mode` input when given."""
    step = [f"- uses: {_ACTION}"]
    if mode is not None:
        step += ["  with:", f"    mode: {mode}"]
    return _workflow(*step)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param(_action(), True, id="mode defaulted"),
        pytest.param(_action("upload"), True, id="mode upload"),
        pytest.param(_action("check"), False, id="mode check"),
        pytest.param(_action("install"), False, id="mode install"),
        pytest.param(
            _workflow(f"# - uses: {_ACTION}", "- run: make test"),
            False,
            id="commented-out action",
        ),
        pytest.param(
            _workflow("- run: cs-coverage upload --format cobertura"),
            True,
            id="command-line upload",
        ),
        pytest.param(
            "# cs-coverage upload is main-only\n" + _workflow("- run: make"),
            False,
            id="commented-out command",
        ),
        pytest.param("on: push\n", False, id="no jobs"),
    ],
)
def test_only_executable_upload_steps_make_a_subject(text: str, expected: bool) -> None:  # noqa: FBT001 - parametrised verdict
    """A mention, a comment or a non-upload mode is not an uploader."""
    assert cv006.workflow_uploads(text) is expected, text


@pytest.mark.parametrize(
    "text", ["jobs: [\n", "- a list\n"], ids=["invalid YAML", "not a mapping"]
)
def test_an_unreadable_workflow_is_refused_rather_than_guessed(text: str) -> None:
    """A workflow whose steps cannot be read cannot clear the subject test."""
    with pytest.raises(cv006.UnreadableWorkflowError):
        cv006.workflow_uploads(text)


def test_fetch_treats_an_unreadable_workflow_as_indeterminate() -> None:
    """Invalid YAML leaves the subject question open, so the result is too."""
    state = _fetch(workflow_texts=("jobs: [\n",))
    assert _statuses(state) == ["indeterminate"]


def test_fetch_does_not_judge_a_check_mode_lane() -> None:
    """A repository whose only CodeScene step checks is not a subject."""
    client = _FakeClient(workflow_texts=(_action("check"),))
    state = cv006.fetch(typ.cast("GithubClient", client), "example", "demo")
    assert state == cv006.CodesceneCredentials(uploads=False)


@pytest.mark.parametrize(
    "failed",
    [
        "workflow_texts",
        "repository_secret_names",
        "environment",
        "environment_branch_policies",
        "environment_secret_names",
    ],
)
def test_fetch_reports_any_failed_read_as_indeterminate(failed: str) -> None:
    """A 5xx, a 429 or a vanished file is indeterminate, not a crash."""
    state = _fetch(**{failed: GithubError("GET x failed: 502")})
    assert state.refused, state
    assert _statuses(state) == ["indeterminate"]
