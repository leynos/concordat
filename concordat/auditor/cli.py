"""CLI entrypoint for the Concordat Auditor GitHub Action."""

from __future__ import annotations

import argparse
import json
import os
import typing as typ
from pathlib import Path

from .checks import build_registry
from .github import DEFAULT_API_URL, GithubClient
from .models import (
    AuditContext,
    BranchProtection,
    CollaboratorPermission,
    LabelState,
    RepositorySnapshot,
    RequiredPullRequestReviews,
    RequiredStatusChecks,
    TeamPermission,
)
from .priority import PriorityModel, load_priority_model
from .sarif import SarifBuilder

DEFAULT_SARIF_PATH = Path("artifacts/concordat-auditor.sarif")
ERROR_TOKEN_REQUIRED = "GITHUB_TOKEN or --token is required when not using a snapshot."  # noqa: S105 - descriptive error message, not a credential
ERROR_REPOSITORY_SLUG = "Invalid repository slug {slug!r}; expected owner/name."


def parse_args(argv: typ.Sequence[str] | None = None) -> argparse.Namespace:
    """Collect CLI arguments for the Concordat Auditor."""
    parser = argparse.ArgumentParser(description="Run Concordat Auditor checks.")
    parser.add_argument(
        "--repository",
        default=os.getenv("GITHUB_REPOSITORY", ""),
        help="Target repository in owner/name form (defaults to GITHUB_REPOSITORY).",
    )
    parser.add_argument(
        "--token",
        default=os.getenv("GITHUB_TOKEN"),
        help="GitHub token; required unless --snapshot is provided.",
    )
    parser.add_argument(
        "--api-url",
        default=os.getenv("GITHUB_API_URL", DEFAULT_API_URL),
        help="GitHub API base URL (defaults to public api.github.com).",
    )
    parser.add_argument(
        "--sarif-path",
        default=DEFAULT_SARIF_PATH,
        type=Path,
        help="Where to write the SARIF log.",
    )
    parser.add_argument(
        "--priority-model",
        default=None,
        type=Path,
        help=(
            "Optional path to platform-standards/canon/priorities/priority-model.yaml. "
            "When omitted, the loader falls back to the default model."
        ),
    )
    parser.add_argument(
        "--snapshot",
        type=Path,
        help="Path to a JSON snapshot used in lieu of live GitHub API calls.",
    )
    parser.add_argument(
        "--fail-on-error",
        action="store_true",
        help="Exit non-zero when any finding has error level.",
    )
    return parser.parse_args(argv)


def main(argv: typ.Sequence[str] | None = None) -> int:
    """Console entrypoint used by the GitHub Action wrapper."""
    args = parse_args(argv)
    owner, repo = _split_repository(args.repository)
    priority_model = load_priority_model(args.priority_model)

    if args.snapshot:
        context = _context_from_snapshot(args.snapshot, priority_model)
    else:
        token = args.token
        if not token:
            raise SystemExit(ERROR_TOKEN_REQUIRED)
        client = GithubClient(token=token, api_url=args.api_url)
        context = _context_from_live_api(client, owner, repo, priority_model)

    registry = build_registry(priority_model)
    findings = registry.evaluate(context)

    sarif = SarifBuilder(tool_name="Concordat Auditor")
    sarif.register_rules(registry.rules)
    sarif.add_findings(
        findings,
        resource_fallback=f"repo:{context.repository.slug}",
    )
    output_path = sarif.write(args.sarif_path)

    print(f"[auditor] wrote {output_path} with {len(findings)} findings")
    error_present = any(finding.level == "error" for finding in findings)
    if args.fail_on_error and error_present:
        return 1
    return 0


def _split_repository(slug: str) -> tuple[str, str]:
    if "/" not in slug:
        message = ERROR_REPOSITORY_SLUG.format(slug=slug)
        raise SystemExit(message)
    owner, name = slug.split("/", 1)
    if not owner or not name:
        message = ERROR_REPOSITORY_SLUG.format(slug=slug)
        raise SystemExit(message)
    return owner, name


def _context_from_snapshot(
    path: Path,
    priority_model: PriorityModel,
) -> AuditContext:
    data = json.loads(path.read_text())
    repository = _repository_from_dict(data["repository"])
    branch_protection = (
        _branch_protection_from_dict(data["branch_protection"])
        if data.get("branch_protection")
        else None
    )
    teams = tuple(
        TeamPermission(slug=entry["slug"], permission=entry["permission"])
        for entry in data.get("teams", [])
    )
    collaborators = tuple(
        CollaboratorPermission(
            login=entry["login"],
            permission=entry.get("permission", ""),
            permissions={
                key: bool(value) for key, value in entry.get("permissions", {}).items()
            },
        )
        for entry in data.get("collaborators", [])
    )
    labels = tuple(
        LabelState(
            name=entry["name"],
            color=str(entry.get("color", "")).lower(),
            description=str(entry.get("description", "")).strip(),
        )
        for entry in data.get("labels", [])
    )
    return AuditContext(
        repository=repository,
        branch_protection=branch_protection,
        teams=teams,
        collaborators=collaborators,
        labels=labels,
        priority_model=priority_model,
    )


def _context_from_live_api(
    client: GithubClient,
    owner: str,
    repo: str,
    priority_model: PriorityModel,
) -> AuditContext:
    repository = client.repository(owner, repo)
    branch_protection = client.branch_protection(owner, repo, repository.default_branch)
    teams = client.teams(owner, repo)
    collaborators = client.outside_collaborators(owner, repo)
    labels = client.labels(owner, repo)
    return AuditContext(
        repository=repository,
        branch_protection=branch_protection,
        teams=teams,
        collaborators=collaborators,
        labels=labels,
        priority_model=priority_model,
    )


def _repository_from_dict(payload: dict[str, object]) -> RepositorySnapshot:
    return RepositorySnapshot(
        owner=str(payload["owner"]),
        name=str(payload["name"]),
        default_branch=str(payload["default_branch"]),
        allow_squash_merge=bool(payload.get("allow_squash_merge", False)),
        allow_merge_commit=bool(payload.get("allow_merge_commit", False)),
        allow_rebase_merge=bool(payload.get("allow_rebase_merge", False)),
        allow_auto_merge=bool(payload.get("allow_auto_merge", False)),
        delete_branch_on_merge=bool(payload.get("delete_branch_on_merge", False)),
    )


def _branch_protection_from_dict(payload: dict[str, object]) -> BranchProtection:
    status_payload_raw = payload.get("status_checks")
    status_payload = (
        typ.cast("dict[str, typ.Any]", status_payload_raw)
        if isinstance(status_payload_raw, dict)
        else None
    )
    review_payload_raw = payload.get("pull_request_reviews")
    review_payload = (
        typ.cast("dict[str, typ.Any]", review_payload_raw)
        if isinstance(review_payload_raw, dict)
        else None
    )
    status_checks = (
        RequiredStatusChecks(
            strict=bool(status_payload.get("strict", False)),
            contexts=tuple(status_payload.get("contexts", [])),
        )
        if status_payload
        else None
    )
    reviews = (
        RequiredPullRequestReviews(
            required_approvals=int(review_payload.get("required_approvals", 0)),
            dismiss_stale_reviews=bool(
                review_payload.get("dismiss_stale_reviews", False)
            ),
            require_code_owner_reviews=bool(
                review_payload.get("require_code_owner_reviews", False)
            ),
        )
        if review_payload
        else None
    )
    signed_commits_field = payload.get("require_signed_commits")
    signed_commits = (
        bool(signed_commits_field) if isinstance(signed_commits_field, bool) else None
    )
    return BranchProtection(
        enforce_admins=bool(payload.get("enforce_admins", False)),
        require_signed_commits=signed_commits,
        required_linear_history=bool(payload.get("required_linear_history", False)),
        require_conversation_resolution=bool(
            payload.get("require_conversation_resolution", False)
        ),
        allows_deletions=bool(payload.get("allows_deletions", False)),
        allows_force_pushes=bool(payload.get("allows_force_pushes", False)),
        status_checks=status_checks,
        pull_request_reviews=reviews,
    )
