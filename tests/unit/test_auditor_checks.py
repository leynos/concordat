"""Unit tests for Auditor checks."""

from __future__ import annotations

import dataclasses
from pathlib import Path

from concordat.auditor import checks
from concordat.auditor.models import (
    AuditContext,
    BranchProtection,
    CollaboratorPermission,
    LabelState,
    RepositorySnapshot,
    RequiredPullRequestReviews,
    RequiredStatusChecks,
    TeamPermission,
)
from concordat.auditor.priority import load_priority_model


def _base_context() -> AuditContext:
    repository = RepositorySnapshot(
        owner="example",
        name="demo",
        default_branch="main",
        allow_squash_merge=True,
        allow_merge_commit=False,
        allow_rebase_merge=False,
        allow_auto_merge=False,
        delete_branch_on_merge=True,
    )
    protection = BranchProtection(
        enforce_admins=True,
        require_signed_commits=True,
        required_linear_history=True,
        require_conversation_resolution=True,
        allows_deletions=False,
        allows_force_pushes=False,
        status_checks=RequiredStatusChecks(
            strict=True, contexts=("concordat/auditor",)
        ),
        pull_request_reviews=RequiredPullRequestReviews(
            required_approvals=2,
            dismiss_stale_reviews=True,
            require_code_owner_reviews=True,
        ),
    )
    teams = (TeamPermission(slug="platform", permission="maintain"),)
    collaborators: tuple[CollaboratorPermission, ...] = ()
    labels = (
        LabelState(
            name="priority/p0-blocker",
            color="b60205",
            description="Blocking incidents.",
        ),
    )
    model = load_priority_model(
        Path("platform-standards/canon/priorities/priority-model.yaml")
    )
    return AuditContext(
        repository=repository,
        branch_protection=protection,
        teams=teams,
        collaborators=collaborators,
        labels=labels,
        priority_model=model,
    )


def test_merge_mode_detects_drift() -> None:
    """Ensure merge strategy deviations produce RS-002 findings."""
    context = _base_context()
    broken_repo = dataclasses.replace(
        context.repository,
        allow_merge_commit=True,
        allow_squash_merge=False,
    )
    context = dataclasses.replace(context, repository=broken_repo)
    registry = checks.build_registry(context.priority_model)
    findings = registry.evaluate(context)
    assert any(item.rule_id == "RS-002" for item in findings)


def test_branch_protection_missing_reports_error() -> None:
    """Verify missing branch protection is surfaced as BP-001."""
    context = _base_context()
    context = dataclasses.replace(context, branch_protection=None)
    registry = checks.build_registry(context.priority_model)
    findings = registry.evaluate(context)
    assert any(item.rule_id == "BP-001" for item in findings)


def test_priority_labels_detect_missing_label() -> None:
    """Missing canonical labels should register LB-001 findings."""
    context = _base_context()
    # Remove all labels to guarantee a finding.
    context = dataclasses.replace(context, labels=())
    registry = checks.build_registry(context.priority_model)
    findings = registry.evaluate(context)
    assert any(item.rule_id == "LB-001" for item in findings)
