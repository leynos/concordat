"""Shared data structures for Auditor checks."""

from __future__ import annotations

import dataclasses
import typing as typ

if typ.TYPE_CHECKING:
    from .priority import PriorityModel

Severity = str


@dataclasses.dataclass(frozen=True)
class RepositorySnapshot:
    """Resolved repository settings fetched from the GitHub API."""

    owner: str
    name: str
    default_branch: str
    allow_squash_merge: bool
    allow_merge_commit: bool
    allow_rebase_merge: bool
    allow_auto_merge: bool
    delete_branch_on_merge: bool

    @property
    def slug(self) -> str:
        """Return the owner/repo slug for SARIF reporting."""
        return f"{self.owner}/{self.name}"


@dataclasses.dataclass(frozen=True)
class RequiredStatusChecks:
    """Minimal representation of GitHub status check requirements."""

    strict: bool
    contexts: tuple[str, ...]


@dataclasses.dataclass(frozen=True)
class RequiredPullRequestReviews:
    """Review requirements configured for branch protection."""

    required_approvals: int
    dismiss_stale_reviews: bool
    require_code_owner_reviews: bool


@dataclasses.dataclass(frozen=True)
class BranchProtection:
    """Branch protection details for the default branch."""

    enforce_admins: bool
    require_signed_commits: bool | None
    required_linear_history: bool
    require_conversation_resolution: bool
    allows_deletions: bool
    allows_force_pushes: bool
    status_checks: RequiredStatusChecks | None
    pull_request_reviews: RequiredPullRequestReviews | None


@dataclasses.dataclass(frozen=True)
class TeamPermission:
    """Team permission assignment exposed by the GitHub API."""

    slug: str
    permission: str


@dataclasses.dataclass(frozen=True)
class CollaboratorPermission:
    """Direct collaborator permission assignment."""

    login: str
    permission: str
    permissions: dict[str, bool]


@dataclasses.dataclass(frozen=True)
class LabelState:
    """GitHub label attributes relevant to Concordat."""

    name: str
    color: str
    description: str


@dataclasses.dataclass(frozen=True)
class AuditContext:
    """Aggregated context handed to each check."""

    repository: RepositorySnapshot
    branch_protection: BranchProtection | None
    teams: tuple[TeamPermission, ...]
    collaborators: tuple[CollaboratorPermission, ...]
    labels: tuple[LabelState, ...]
    priority_model: PriorityModel | None


@dataclasses.dataclass(frozen=True)
class CheckDefinition:
    """Metadata describing a Concordat Auditor rule."""

    rule_id: str
    name: str
    short_description: str
    long_description: str
    level: Severity
    help_uri: str | None = None


@dataclasses.dataclass(frozen=True)
class Finding:
    """Single SARIF finding emitted by a check."""

    rule_id: str
    message: str
    level: Severity
    resource: str | None = None
    properties: dict[str, typ.Any] | None = None
