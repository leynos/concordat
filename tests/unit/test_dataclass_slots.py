"""Contracts for production dataclasses whose storage is explicitly slotted."""

from __future__ import annotations

import typing as typ

import pytest

from concordat import enrol, estate_execution, platform_standards
from concordat.auditor import models as auditor_models
from concordat.auditor import priority
from concordat.persistence import models as persistence_models


@pytest.mark.parametrize(
    ("model_type", "expected_fields"),
    [
        pytest.param(
            auditor_models.RepositorySnapshot,
            (
                "owner",
                "name",
                "default_branch",
                "allow_squash_merge",
                "allow_merge_commit",
                "allow_rebase_merge",
                "allow_auto_merge",
                "delete_branch_on_merge",
            ),
            id="repository-snapshot",
        ),
        pytest.param(
            auditor_models.RequiredStatusChecks,
            ("strict", "contexts"),
            id="required-status-checks",
        ),
        pytest.param(
            auditor_models.RequiredPullRequestReviews,
            (
                "required_approvals",
                "dismiss_stale_reviews",
                "require_code_owner_reviews",
            ),
            id="required-pull-request-reviews",
        ),
        pytest.param(
            auditor_models.BranchProtection,
            (
                "enforce_admins",
                "require_signed_commits",
                "required_linear_history",
                "require_conversation_resolution",
                "allows_deletions",
                "allows_force_pushes",
                "status_checks",
                "pull_request_reviews",
            ),
            id="branch-protection",
        ),
        pytest.param(
            auditor_models.TeamPermission,
            ("slug", "permission"),
            id="team-permission",
        ),
        pytest.param(
            auditor_models.CollaboratorPermission,
            ("login", "permission", "permissions"),
            id="collaborator-permission",
        ),
        pytest.param(
            auditor_models.LabelState,
            ("name", "color", "description"),
            id="label-state",
        ),
        pytest.param(
            auditor_models.AuditContext,
            (
                "repository",
                "branch_protection",
                "teams",
                "collaborators",
                "labels",
                "priority_model",
            ),
            id="audit-context",
        ),
        pytest.param(
            auditor_models.CheckDefinition,
            (
                "rule_id",
                "name",
                "short_description",
                "long_description",
                "level",
                "help_uri",
            ),
            id="check-definition",
        ),
        pytest.param(
            auditor_models.Finding,
            ("rule_id", "message", "level", "resource", "properties"),
            id="finding",
        ),
        pytest.param(
            priority.PriorityLabel,
            ("key", "name", "color", "description"),
            id="priority-label",
        ),
        pytest.param(
            priority.PriorityFieldOption,
            ("key", "display_name"),
            id="priority-field-option",
        ),
        pytest.param(
            priority.PriorityField,
            ("name", "type", "options"),
            id="priority-field",
        ),
        pytest.param(
            priority.PriorityModel,
            ("schema_version", "labels", "field", "aliases"),
            id="priority-model",
        ),
        pytest.param(
            enrol.EnrollmentOutcome,
            ("repository", "location", "created", "committed", "pushed", "platform_pr"),
            id="enrollment-outcome",
        ),
        pytest.param(
            enrol.DisenrollmentOutcome,
            (
                "repository",
                "location",
                "updated",
                "missing_document",
                "committed",
                "pushed",
                "platform_pr",
            ),
            id="disenrollment-outcome",
        ),
        pytest.param(
            enrol._RepositoryContext,
            ("repository", "location", "is_remote", "callbacks"),
            id="repository-context",
        ),
        pytest.param(
            estate_execution.ExecutionOptions,
            (
                "github_owner",
                "github_token",
                "extra_args",
                "keep_workdir",
                "cache_directory",
                "environment",
            ),
            id="execution-options",
        ),
        pytest.param(
            estate_execution.ExecutionIO,
            ("stdout", "stderr"),
            id="execution-io",
        ),
        pytest.param(
            estate_execution.WorkspaceContext,
            ("root", "tofu_dir"),
            id="execution-workspace-context",
        ),
        pytest.param(
            estate_execution.ExecutionContext,
            ("options", "io", "env"),
            id="execution-context",
        ),
        pytest.param(
            estate_execution.PersistenceRuntime,
            ("descriptor", "backend_config", "object_key", "env_overrides"),
            id="execution-persistence-runtime",
        ),
        pytest.param(
            persistence_models.PersistenceDescriptor,
            (
                "schema_version",
                "enabled",
                "bucket",
                "key_prefix",
                "key_suffix",
                "region",
                "endpoint",
                "backend_config_path",
                "notification_topic",
            ),
            id="persistence-descriptor",
        ),
        pytest.param(
            persistence_models.PersistenceResult,
            ("backend_path", "manifest_path", "branch", "pr_url", "updated", "message"),
            id="persistence-result",
        ),
        pytest.param(
            persistence_models.PersistenceFiles,
            ("backend_path", "backend_contents", "manifest_path", "manifest_contents"),
            id="persistence-files",
        ),
        pytest.param(
            persistence_models.PersistenceOptions,
            (
                "force",
                "github_token",
                "input_func",
                "s3_client_factory",
                "pr_opener",
                "fmt_runner",
                "timestamp_factory",
                "allow_insecure_endpoint",
                "bucket",
                "region",
                "endpoint",
                "key_prefix",
                "key_suffix",
                "no_input",
            ),
            id="persistence-options",
        ),
        pytest.param(
            persistence_models.PullRequestContext,
            (
                "record",
                "branch_name",
                "descriptor",
                "key_suffix",
                "github_token",
                "pr_opener",
            ),
            id="pull-request-context",
        ),
        pytest.param(
            persistence_models.PersistencePaths,
            ("manifest_path", "backend_path"),
            id="persistence-paths",
        ),
        pytest.param(
            persistence_models.WorkspaceContext,
            ("workdir", "repository"),
            id="persistence-workspace-context",
        ),
        pytest.param(
            persistence_models.FinalizationContext,
            (
                "record",
                "branch_name",
                "descriptor",
                "key_suffix",
                "github_token",
                "opts",
            ),
            id="finalization-context",
        ),
        pytest.param(
            platform_standards.PlatformStandardsConfig,
            ("repo_url", "base_branch", "inventory_path", "github_token"),
            id="platform-standards-config",
        ),
        pytest.param(
            platform_standards.PlatformStandardsResult,
            ("created", "branch", "pr_url", "message"),
            id="platform-standards-result",
        ),
    ],
)
def test_recent_dataclasses_store_their_declared_fields_in_slots(
    model_type: type[object],
    expected_fields: tuple[str, ...],
) -> None:
    """Keep each changed dataclass's declared fields in slotted storage."""
    actual_fields = tuple(
        typ.cast("dict[str, object]", model_type.__dict__["__dataclass_fields__"])
    )
    slots = typ.cast("tuple[str, ...]", model_type.__dict__["__slots__"])
    instance = object.__new__(model_type)

    assert actual_fields == expected_fields
    assert slots == expected_fields
    with pytest.raises(AttributeError):
        object.__setattr__(instance, "undeclared_field", None)
