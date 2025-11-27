"""Pull request helpers for persistence workflow."""

from __future__ import annotations

import importlib
import textwrap
import typing as typ

import github3

from concordat.platform_standards import parse_github_slug

if typ.TYPE_CHECKING:
    from concordat.persistence.models import PullRequestContext


def _open_pr_if_configured(context: PullRequestContext) -> str | None:
    """Open a pull request if token or custom opener is provided."""
    if not (context.github_token or context.pr_opener):
        return None
    persistence_pkg = importlib.import_module("concordat.persistence")
    opener = context.pr_opener or persistence_pkg._open_pr
    return opener(context)


def _open_pr(context: PullRequestContext) -> str | None:
    slug = parse_github_slug(context.record.repo_url)
    if not slug or not context.github_token:
        return None
    owner, name = slug.split("/", 1)
    client = github3.login(token=context.github_token)
    gh_repo = client.repository(owner, name)
    title = "Concordat: persist estate remote state"
    key = (
        f"{context.descriptor.key_prefix.rstrip('/')}/{context.key_suffix.lstrip('/')}"
    )
    body = textwrap.dedent(
        f"""
        This pull request enables remote state for the estate.

        - bucket: `{context.descriptor.bucket}`
        - key: `{key}`
        - region: `{context.descriptor.region}`
        - endpoint: `{context.descriptor.endpoint}`

        Credentials are expected via environment variables; none are written to
        the repository.
        """
    ).strip()
    pr = gh_repo.create_pull(
        title,
        base=context.record.branch,
        head=context.branch_name,
        body=body,
    )
    return pr.html_url


def _build_result_message(pr_url: str | None) -> str:
    """Build the result message based on PR creation status."""
    return "opened persistence pull request" if pr_url else "pushed branch"
