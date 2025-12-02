"""Pull-request helper coverage for persistence."""

from __future__ import annotations

import concordat.persistence.pr as persistence_pr
from concordat import persistence
from concordat.estate import EstateRecord


def test_open_pr_returns_none_without_token() -> None:
    """_open_pr gracefully skips when token missing."""
    descriptor = persistence.PersistenceDescriptor(
        schema_version=persistence.PERSISTENCE_SCHEMA_VERSION,
        enabled=True,
        bucket="df12",
        key_prefix="estates/example/main",
        key_suffix="terraform.tfstate",
        region="fr-par",
        endpoint="https://s3.fr-par.scw.cloud",
        backend_config_path="backend.tfbackend",
    )
    record = EstateRecord(
        alias="core",
        repo_url="git@github.com:example/core.git",
        github_owner="example",
    )
    context = persistence.PullRequestContext(
        record=record,
        branch_name="branch",
        descriptor=descriptor,
        key_suffix="terraform.tfstate",
        github_token=None,
    )
    result = persistence_pr._open_pr(context)
    assert result is None
