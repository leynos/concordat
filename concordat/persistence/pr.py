"""Pull request helpers for persistence workflow."""

from __future__ import annotations

import importlib
import textwrap
import typing as typ

import github3

from concordat.platform_standards import parse_github_slug

if typ.TYPE_CHECKING:
    from concordat.estate import EstateRecord
    from concordat.persistence.models import PersistenceDescriptor


def _open_pr_if_configured(
    *,
    record: EstateRecord,
    branch_name: str,
    descriptor: PersistenceDescriptor,
    key_suffix: str,
    github_token: str | None,
    pr_opener: typ.Callable[..., str | None] | None,
) -> str | None:
    """Open a pull request if token or custom opener is provided."""
    if not (github_token or pr_opener):
        return None
    persistence_pkg = importlib.import_module("concordat.persistence")
    opener = pr_opener or persistence_pkg._open_pr
    return opener(
        record=record,
        branch_name=branch_name,
        descriptor=descriptor,
        key_suffix=key_suffix,
        github_token=github_token,
    )


def _open_pr(
    *,
    record: EstateRecord,
    branch_name: str,
    descriptor: PersistenceDescriptor,
    key_suffix: str,
    github_token: str | None,
) -> str | None:
    slug = parse_github_slug(record.repo_url)
    if not slug or not github_token:
        return None
    owner, name = slug.split("/", 1)
    client = github3.login(token=github_token)
    gh_repo = client.repository(owner, name)
    title = "Concordat: persist estate remote state"
    key = f"{descriptor.key_prefix.rstrip('/')}/{key_suffix.lstrip('/')}"
    body = textwrap.dedent(
        f"""
        This pull request enables remote state for the estate.

        - bucket: `{descriptor.bucket}`
        - key: `{key}`
        - region: `{descriptor.region}`
        - endpoint: `{descriptor.endpoint}`

        Credentials are expected via environment variables; none are written to
        the repository.
        """
    ).strip()
    pr = gh_repo.create_pull(
        title,
        base=record.branch,
        head=branch_name,
        body=body,
    )
    return pr.html_url


def _build_result_message(pr_url: str | None) -> str:
    """Build the result message based on PR creation status."""
    return "opened persistence pull request" if pr_url else "pushed branch"
