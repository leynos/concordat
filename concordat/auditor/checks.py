"""Auditor checks for repository, branch, permission, and label state."""

from __future__ import annotations

import typing as typ

from . import codescene_environment
from .models import AuditContext, CheckDefinition, Finding

if typ.TYPE_CHECKING:
    from .models import (
        BranchProtection,
        RequiredPullRequestReviews,
        RequiredStatusChecks,
    )

if typ.TYPE_CHECKING:
    from .priority import PriorityModel

DOC_URL = "https://github.com/leynos/concordat/blob/main/docs/concordat-design.md"


class CheckRegistry:
    """Helper to register and execute Concordat Auditor checks."""

    def __init__(self) -> None:
        """Initialise an empty registry."""
        self._entries: list[
            tuple[CheckDefinition, typ.Callable[[AuditContext], list[Finding]]]
        ] = []

    def register(
        self,
        definition: CheckDefinition,
        handler: typ.Callable[[AuditContext], list[Finding]],
    ) -> None:
        """Register a new check handler."""
        self._entries.append((definition, handler))

    @property
    def rules(self) -> list[CheckDefinition]:
        """Expose the rule metadata for SARIF output."""
        return [entry[0] for entry in self._entries]

    def evaluate(self, context: AuditContext) -> list[Finding]:
        """Run every registered handler and aggregate the findings."""
        findings: list[Finding] = []
        for _definition, handler in self._entries:
            result = handler(context)
            findings.extend(result)
        return findings


def build_registry(priority_model: PriorityModel | None) -> CheckRegistry:
    """Build the default set of checks for the Auditor."""
    registry = CheckRegistry()
    registry.register(_rule_default_branch(), _run_default_branch)
    registry.register(_rule_merge_mode(), _run_merge_mode)
    registry.register(_rule_branch_protection(), _run_branch_protection)
    registry.register(_rule_permissions(), _run_permissions)
    registry.register(codescene_environment.rule(), codescene_environment.run)
    if priority_model:
        registry.register(
            _rule_priority_labels(),
            lambda context: _run_priority_labels(context, priority_model),
        )
    return registry


def _rule_default_branch() -> CheckDefinition:
    """Describe RS-001 for the SARIF rule catalogue."""
    return CheckDefinition(
        rule_id="RS-001",
        name="Default branch is main",
        short_description="Repositories standardise on a `main` default branch.",
        long_description=(
            "Concordat repositories must use `main` as the default branch so that "
            "shared tooling (rulesets, CI jobs, and drift detection) can rely on a "
            "single branch pattern. See Table 3 in the design document."
        ),
        level="error",
        help_uri=f"{DOC_URL}#table-3-auditor-check-catalog",
    )


def _rule_merge_mode() -> CheckDefinition:
    """Describe RS-002 for the SARIF rule catalogue."""
    return CheckDefinition(
        rule_id="RS-002",
        name="Repository merge strategy baseline",
        short_description=(
            "Only squash merges are enabled; other strategies stay disabled."
        ),
        long_description=(
            "The OpenTofu repository module enforces squash merges and disables "
            "merge commits, rebase merges, and automatic merges. The Auditor cross-"
            "checks this configuration to detect manual drift."
        ),
        level="error",
        help_uri=f"{DOC_URL}#table-3-auditor-check-catalog",
    )


def _rule_branch_protection() -> CheckDefinition:
    """Describe BP-001 for the SARIF rule catalogue."""
    return CheckDefinition(
        rule_id="BP-001",
        name="Default branch protection baseline",
        short_description=(
            "Default branch enforces reviews, status checks, and admin parity."
        ),
        long_description=(
            "Concordat branch protection keeps admins under the same guardrails, "
            "forces signed commits and linear history, requires two approvals and "
            "CODEOWNERS reviews, and locks force pushes unless explicitly exempted."
        ),
        level="error",
        help_uri=f"{DOC_URL}#step-standardize-branch-protections",
    )


def _rule_permissions() -> CheckDefinition:
    """Describe PM-001 for the SARIF rule catalogue."""
    return CheckDefinition(
        rule_id="PM-001",
        name="Team-managed access and no unmanaged admins",
        short_description=(
            "Admin access routes through teams; outside admins are disallowed."
        ),
        long_description=(
            "Manual admin access bypasses declarative policy. The Auditor requires "
            "each repository to expose a maintain/admin team assignment and rejects "
            "outside collaborators with admin scope."
        ),
        level="error",
        help_uri=f"{DOC_URL}#42-managing-teams-and-permissions",
    )


def _rule_priority_labels() -> CheckDefinition:
    """Describe LB-001 for the SARIF rule catalogue."""
    return CheckDefinition(
        rule_id="LB-001",
        name="Canonical priority labels exist",
        short_description=(
            "priority/p0 through priority/p3 must match the canonical model."
        ),
        long_description=(
            "Every managed repository must expose the canonical priority labels with "
            "the names, colours, and descriptions defined in `priority-model.yaml`. "
            "This keeps drift visible before the OpenTofu rollout."
        ),
        level="warning",
        help_uri=f"{DOC_URL}#21-motivation-and-scope",
    )


def _run_default_branch(context: AuditContext) -> list[Finding]:
    """Report a default branch other than `main` (RS-001)."""
    repo = context.repository
    if repo.default_branch == "main":
        return []
    message = (
        f"Default branch is {repo.default_branch!r}; expected 'main' for {repo.slug}."
    )
    return [
        Finding(
            rule_id="RS-001",
            message=message,
            level="error",
            resource=f"repo:{repo.slug}",
            properties={"default_branch": repo.default_branch},
        )
    ]


def _run_merge_mode(context: AuditContext) -> list[Finding]:
    """Report merge settings that depart from squash-only (RS-002)."""
    repo = context.repository
    findings: list[Finding] = []
    if not repo.allow_squash_merge:
        findings.append(
            Finding(
                rule_id="RS-002",
                message="allow_squash_merge is disabled; Concordat requires it.",
                level="error",
                resource=f"repo:{repo.slug}",
            )
        )
    if repo.allow_merge_commit:
        findings.append(
            Finding(
                rule_id="RS-002",
                message=(
                    "allow_merge_commit is enabled; disable merge commits for "
                    "squash-only flow."
                ),
                level="error",
                resource=f"repo:{repo.slug}",
            )
        )
    if repo.allow_rebase_merge:
        findings.append(
            Finding(
                rule_id="RS-002",
                message=(
                    "allow_rebase_merge is enabled; Concordat enforces squash merges."
                ),
                level="error",
                resource=f"repo:{repo.slug}",
            )
        )
    if repo.allow_auto_merge:
        findings.append(
            Finding(
                rule_id="RS-002",
                message=(
                    "allow_auto_merge should remain disabled unless explicitly "
                    "approved."
                ),
                level="warning",
                resource=f"repo:{repo.slug}",
            )
        )
    if not repo.delete_branch_on_merge:
        findings.append(
            Finding(
                rule_id="RS-002",
                message=(
                    "delete_branch_on_merge is disabled; enable it to enforce "
                    "branch hygiene."
                ),
                level="warning",
                resource=f"repo:{repo.slug}",
            )
        )
    return findings


def _run_branch_protection(context: AuditContext) -> list[Finding]:
    """Report each gap in the default branch's protection (BP-001)."""
    repo = context.repository
    protection = context.branch_protection
    resource = f"branch:{repo.slug}@{repo.default_branch}"
    if protection is None:
        return [
            _branch_finding(
                resource, f"No branch protection configured for {resource}.", "error"
            )
        ]
    gaps = (
        _protection_gaps(protection)
        + _status_check_gaps(protection.status_checks)
        + _review_gaps(protection.pull_request_reviews)
    )
    return [_branch_finding(resource, message, level) for message, level in gaps]


def _branch_finding(resource: str, message: str, level: str) -> Finding:
    """Build one BP-001 finding for the default branch."""
    return Finding(rule_id="BP-001", message=message, level=level, resource=resource)


def _failed(rules: tuple[tuple[bool, str, str], ...]) -> list[tuple[str, str]]:
    """Return the message and level of each rule whose condition failed."""
    return [(message, level) for failed, message, level in rules if failed]


def _protection_gaps(protection: BranchProtection) -> list[tuple[str, str]]:
    """Report the protection flags that depart from the baseline, in order."""
    return _failed((
        (
            not protection.enforce_admins,
            "Admins bypass branch protection; enable enforce_admins.",
            "error",
        ),
        (
            protection.require_signed_commits is not True,
            "Signed commits are not enforced on the default branch.",
            "error",
        ),
        (
            not protection.required_linear_history,
            (
                "Linear history is disabled; enable it to preserve the "
                "squash-only workflow."
            ),
            "error",
        ),
        (
            not protection.require_conversation_resolution,
            "Conversation resolution is not required before merging.",
            "warning",
        ),
        (
            protection.allows_force_pushes,
            "Force pushes are allowed; disable them to protect the default branch.",
            "error",
        ),
        (
            protection.allows_deletions,
            "Branch deletions are allowed; Concordat keeps protected branches durable.",
            "warning",
        ),
    ))


def _status_check_gaps(
    status_checks: RequiredStatusChecks | None,
) -> list[tuple[str, str]]:
    """Report missing strict status checks, or the Auditor's missing context."""
    if status_checks is None or not status_checks.strict:
        return [("Strict required status checks are missing.", "error")]
    required_context = "concordat/auditor"
    if required_context not in status_checks.contexts:
        return [(f"Required status checks omit {required_context!r}.", "warning")]
    return []


def _review_gaps(
    reviews: RequiredPullRequestReviews | None,
) -> list[tuple[str, str]]:
    """Report missing or weak pull request review requirements, in order."""
    if reviews is None:
        return [("Pull request review requirements are missing.", "error")]
    return _failed((
        (
            reviews.required_approvals < 2,
            "At least two approvals are required on the default branch.",
            "error",
        ),
        (
            not reviews.dismiss_stale_reviews,
            "Dismiss stale reviews to prevent outdated approvals.",
            "warning",
        ),
        (
            not reviews.require_code_owner_reviews,
            "Require CODEOWNERS reviews to enforce ownership boundaries.",
            "error",
        ),
    ))


def _run_permissions(context: AuditContext) -> list[Finding]:
    """Report missing team access and outside admins (PM-001)."""
    repo = context.repository
    resource = f"repo:{repo.slug}"
    findings: list[Finding] = []
    admin_teams = [
        team for team in context.teams if team.permission in {"admin", "maintain"}
    ]
    if not admin_teams:
        findings.append(
            Finding(
                rule_id="PM-001",
                message=(
                    "No team has maintain/admin access; delegate permissions via "
                    "OpenTofu-managed teams."
                ),
                level="error",
                resource=resource,
            )
        )
    findings.extend([
        Finding(
            rule_id="PM-001",
            message=(f"Outside collaborator {collaborator.login} has admin access."),
            level="error",
            resource=resource,
            properties={"login": collaborator.login},
        )
        for collaborator in context.collaborators
        if collaborator.permissions.get("admin", False)
    ])
    return findings


def _run_priority_labels(
    context: AuditContext,
    model: PriorityModel,
) -> list[Finding]:
    """Report priority labels missing or drifted from the model (LB-001)."""
    repo = context.repository
    resource = f"repo:{repo.slug}"
    labels = {label.name: label for label in context.labels}
    findings: list[Finding] = []
    for canonical in model.labels:
        existing = labels.get(canonical.name)
        if not existing:
            findings.append(
                Finding(
                    rule_id="LB-001",
                    message=f"Missing canonical priority label {canonical.name!r}.",
                    level="warning",
                    resource=resource,
                    properties={"expected": canonical.name},
                )
            )
            continue
        if existing.color.lower() != canonical.color.lower():
            findings.append(
                Finding(
                    rule_id="LB-001",
                    message=(
                        f"Label {canonical.name} colour {existing.color} "
                        f"differs from canonical {canonical.color}."
                    ),
                    level="warning",
                    resource=resource,
                    properties={
                        "expected": canonical.color,
                        "actual": existing.color,
                    },
                )
            )
        normalized_expected = canonical.description.strip()
        normalized_actual = existing.description.strip()
        if normalized_expected and normalized_actual != normalized_expected:
            findings.append(
                Finding(
                    rule_id="LB-001",
                    message=(
                        f"Label {canonical.name} description differs from the "
                        "canonical text."
                    ),
                    level="note",
                    resource=resource,
                )
            )
    return findings
