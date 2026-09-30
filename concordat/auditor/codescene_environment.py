"""CV-006: the CodeScene token lives in a main-only `codescene` environment.

CV-005 holds the publisher workflow to declaring `environment: codescene`
on its upload job. Whether that environment exists, admits `main` alone,
and holds `CS_ACCESS_TOKEN` is repository configuration, which no checkout
carries, so the Auditor reads it through the API. Secret names are read,
never values.

The check tolerates the estate's move in progress: an environment that is
ready while the token is still a repository secret is reported as
"secret not yet moved", distinct from a missing environment, so a rollout
reads as progress. A read that is refused or fails is reported as
indeterminate, never as a pass, and never aborts the rest of the audit.
"""

from __future__ import annotations

import dataclasses
import re
import typing as typ

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from .github import GithubError
from .models import CheckDefinition, Finding

if typ.TYPE_CHECKING:
    from .github import GithubClient
    from .models import AuditContext

RULE_ID: typ.Final = "CV-006"
ENVIRONMENT: typ.Final = "codescene"
# The credential's name, not a credential.
TOKEN_NAME: typ.Final = "CS_ACCESS_TOKEN"  # ruff: ignore[hardcoded-password-string] - a secret's name
MAIN_ONLY: typ.Final = (("main", "branch"),)
UPLOAD_ACTION: typ.Final = "upload-codescene-coverage"
_CLI_UPLOAD: typ.Final = re.compile(r"(^|[\s;&|()])cs-coverage\s+upload(\s|$)")
_yaml = YAML(typ="safe")


class UnreadableWorkflowError(ValueError):
    """Raised when a workflow's text cannot be read as a YAML document."""


def _step_uploads(step: object) -> bool:
    """Return whether one workflow step uploads to CodeScene.

    The action uploads unless its `mode` input says otherwise (`check` and
    `install` do not), and a `run` step uploads when a command line starts
    `cs-coverage upload`. YAML comments never reach this, since the text is
    parsed first.

    Returns
    -------
    bool
        Whether the step uploads.
    """
    if not isinstance(step, dict):
        return False
    uses = step.get("uses")
    if isinstance(uses, str) and UPLOAD_ACTION in uses.lower():
        inputs = step.get("with")
        mode = inputs.get("mode", "upload") if isinstance(inputs, dict) else "upload"
        return str(mode).strip().lower() == "upload"
    run = step.get("run")
    return isinstance(run, str) and _CLI_UPLOAD.search(run) is not None


def workflow_uploads(text: str) -> bool:
    """Return whether a workflow's executable steps upload to CodeScene.

    A text that is not a YAML mapping cannot have its steps read, so
    `_document` raises `UnreadableWorkflowError` rather than this answering.

    Returns
    -------
    bool
        Whether any step of any job uploads.
    """
    return any(_step_uploads(step) for step in _steps(_document(text)))


def _document(text: str) -> dict[object, object]:
    """Parse a workflow into its top-level mapping.

    Returns
    -------
    dict[object, object]
        The decoded document.

    Raises
    ------
    UnreadableWorkflowError
        If the text is not YAML, or not a mapping.
    """
    try:
        document = _yaml.load(text)
    except YAMLError as error:
        raise UnreadableWorkflowError(str(error)) from error
    if not isinstance(document, dict):
        message = "workflow is not a mapping"
        raise UnreadableWorkflowError(message)
    return document


def _steps(document: dict[object, object]) -> list[object]:
    """Return every step of every job, in document order."""
    jobs = document.get("jobs")
    if not isinstance(jobs, dict):
        return []
    steps: list[object] = []
    for job in jobs.values():
        declared = job.get("steps") if isinstance(job, dict) else None
        if isinstance(declared, list):
            steps.extend(declared)
    return steps


DOC_URL: typ.Final = (
    "https://github.com/leynos/concordat/blob/main/docs/concordat-design.md"
)


@dataclasses.dataclass(frozen=True, slots=True)
class CodesceneCredentials:
    """What the repository settings say about the CodeScene token's home.

    Attributes
    ----------
    uploads:
        Whether any root workflow uploads to CodeScene. Only such a
        repository is a subject of CV-006.
    environment_exists:
        Whether the `codescene` environment exists.
    protected_branches:
        Whether the environment's branch policy is the protected-branches
        shortcut, which admits any protected branch rather than `main`.
    custom_branch_policies:
        Whether the environment uses custom branch policies.
    branch_policies:
        The custom policies as (name pattern, type) pairs.
    environment_secrets:
        The names of the environment's secrets.
    repository_secrets:
        The names of the repository's Actions secrets.
    refused:
        The reads that were refused (401 or 403) or failed (any other API
        error, or a workflow that is not YAML), which make the result
        indeterminate.
    """

    uploads: bool
    environment_exists: bool = False
    protected_branches: bool = False
    custom_branch_policies: bool = False
    branch_policies: tuple[tuple[str, str], ...] = ()
    environment_secrets: tuple[str, ...] = ()
    repository_secrets: tuple[str, ...] = ()
    refused: tuple[str, ...] = ()


def fetch(client: GithubClient, owner: str, name: str) -> CodesceneCredentials:
    """Read the CodeScene credential settings for one repository.

    A refused or failed read is recorded rather than raised, so the check
    reports it as indeterminate and the rest of the audit still runs. A
    repository whose workflow steps upload nothing is not read further.

    Returns
    -------
    CodesceneCredentials
        The settings, or the reads that were refused.
    """
    try:
        uploads = any(
            workflow_uploads(text) for text in client.workflow_texts(owner, name)
        )
    except (GithubError, UnreadableWorkflowError):
        return CodesceneCredentials(uploads=True, refused=("workflows",))
    if not uploads:
        return CodesceneCredentials(uploads=False)
    reader = _SettingsReader(client, owner, name)
    repository_secrets = reader.read(
        "repository secrets", lambda: client.repository_secret_names(owner, name), ()
    )
    environment = reader.read(
        "environment", lambda: client.environment(owner, name, ENVIRONMENT), None
    )
    settings = reader.environment_settings(environment) if environment else {}
    return CodesceneCredentials(
        uploads=True,
        repository_secrets=repository_secrets,
        refused=tuple(reader.refused),
        **settings,
    )


class _SettingsReader:
    """Read one repository's settings, recording each refused read."""

    def __init__(self, client: GithubClient, owner: str, name: str) -> None:
        """Bind the reader to one repository."""
        self.client = client
        self.owner = owner
        self.name = name
        self.refused: list[str] = []

    def read[T](self, label: str, read: typ.Callable[[], T], default: T) -> T:
        """Return one read's result, or *default* when it is refused."""
        try:
            return read()
        except GithubError:
            self.refused.append(label)
            return default

    def environment_settings(
        self, environment: dict[str, typ.Any]
    ) -> dict[str, typ.Any]:
        """Return the environment's branch policy and secret names.

        The custom policies are listed only under a custom policy: the
        protected-branches shortcut has none to list.

        Returns
        -------
        dict[str, typing.Any]
            The `CodesceneCredentials` fields describing the environment.
        """
        policy = environment.get("deployment_branch_policy") or {}
        custom = bool(policy.get("custom_branch_policies", False))
        client, owner, name = self.client, self.owner, self.name
        policies = (
            self.read(
                "branch policies",
                lambda: client.environment_branch_policies(owner, name, ENVIRONMENT),
                (),
            )
            if custom
            else ()
        )
        return {
            "environment_exists": True,
            "protected_branches": bool(policy.get("protected_branches", False)),
            "custom_branch_policies": custom,
            "branch_policies": policies,
            "environment_secrets": self.read(
                "environment secrets",
                lambda: client.environment_secret_names(owner, name, ENVIRONMENT),
                (),
            ),
        }


def rule() -> CheckDefinition:
    """Describe CV-006 for the SARIF rule catalogue."""
    return CheckDefinition(
        rule_id=RULE_ID,
        name="CodeScene token lives in a main-only environment",
        short_description=(
            "CS_ACCESS_TOKEN is a secret of environment codescene, whose branch "
            "policy admits main alone."
        ),
        long_description=(
            "A repository secret is readable from any branch, and anyone who can "
            "push can dispatch a branch copy of the publisher. The token therefore "
            "lives in the `codescene` environment, whose deployment branch policy "
            "is a custom policy admitting `main` alone, and is not a repository "
            "secret. CV-005 requires the upload job to declare the environment."
        ),
        level="error",
        help_uri=f"{DOC_URL}#coverage-pipeline-reach-cv-001-through-cv-005",
    )


def run(context: AuditContext) -> list[Finding]:
    """Report how far the repository is from the main-only token home.

    Returns
    -------
    list[Finding]
        The findings; none for a compliant repository or one that uploads
        nothing.
    """
    state = context.codescene
    if state is None or not state.uploads:
        return []
    resource = f"repo:{context.repository.slug}"
    if state.refused:
        return [_finding(resource, "indeterminate", _refused_message(state), "warning")]
    if not state.environment_exists:
        return [
            _finding(
                resource,
                "environment-missing",
                f"environment {ENVIRONMENT} does not exist; create it with a "
                "deployment branch policy admitting main alone",
            )
        ]
    return _policy_findings(state, resource) + _secret_findings(state, resource)


def _policy_findings(state: CodesceneCredentials, resource: str) -> list[Finding]:
    """Report a branch policy that admits anything but `main`."""
    if state.protected_branches or not state.custom_branch_policies:
        return [
            _finding(
                resource,
                "policy-not-custom",
                f"environment {ENVIRONMENT} must use a custom deployment branch "
                "policy, not protected branches or no policy",
            )
        ]
    if state.branch_policies != MAIN_ONLY:
        return [
            _finding(
                resource,
                "policy-not-main-only",
                f"environment {ENVIRONMENT} admits {list(state.branch_policies)}; "
                "it must admit the branch main alone",
            )
        ]
    return []


def _secret_findings(state: CodesceneCredentials, resource: str) -> list[Finding]:
    """Report where the token sits, when that is not the environment alone."""
    if TOKEN_NAME in state.repository_secrets:
        return [
            _finding(
                resource,
                "secret-not-moved",
                f"secret not yet moved: {TOKEN_NAME} is still a repository secret; "
                f"move it into environment {ENVIRONMENT} and delete the repository "
                "copy",
            )
        ]
    if TOKEN_NAME not in state.environment_secrets:
        return [
            _finding(
                resource,
                "secret-missing",
                f"{TOKEN_NAME} is not a secret of environment {ENVIRONMENT}",
            )
        ]
    return []


def _refused_message(state: CodesceneCredentials) -> str:
    """Name the reads that were refused or failed."""
    reads = ", ".join(state.refused)
    return (
        f"cannot tell whether {TOKEN_NAME} lives in environment {ENVIRONMENT}: "
        f"could not read {reads}"
    )


def _finding(resource: str, status: str, message: str, level: str = "error") -> Finding:
    """Build one CV-006 finding carrying its machine-readable status."""
    return Finding(
        rule_id=RULE_ID,
        message=message,
        level=level,
        resource=resource,
        properties={"status": status},
    )
