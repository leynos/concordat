"""Workflow-level tests for persistence operations."""

from __future__ import annotations

import typing as typ

import pytest

import concordat.persistence.gitops as gitops
import concordat.persistence.workflow as persistence_workflow
from concordat import estate_execution, persistence
from concordat.estate import EstateRecord
from tests.unit.conftest import PersistTestContext, _make_repo

if typ.TYPE_CHECKING:
    from pathlib import Path


def test_setup_persistence_environment_rejects_dirty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dirty cached estate raises a PersistenceError."""
    _make_repo(tmp_path)
    readme = tmp_path / "README.md"
    readme.write_text("dirty\n", encoding="utf-8")
    record = EstateRecord(
        alias="core",
        repo_url=str(tmp_path),
        github_owner="example",
    )

    monkeypatch.setattr(
        estate_execution,
        "ensure_estate_cache",
        lambda record: tmp_path,
    )

    with pytest.raises(persistence.PersistenceError):
        persistence_workflow._load_clean_estate(record)


def test_persist_estate_uses_env_token_and_remote(
    monkeypatch: pytest.MonkeyPatch,
    persist_test_context: PersistTestContext,
) -> None:
    """persist_estate falls back to GITHUB_TOKEN and respects custom remotes."""
    ctx = persist_test_context

    monkeypatch.setenv("GITHUB_TOKEN", "env-token")

    pr_log: dict[str, str | None] = {}

    def pr_opener(context: persistence.PullRequestContext) -> str:
        pr_log["github_token"] = context.github_token
        pr_log["branch_name"] = context.branch_name
        return "https://example.test/pr/1"

    push_calls: list[tuple[str, str]] = []

    monkeypatch.setattr(
        gitops,
        "_push_branch",
        lambda repository, branch, repo_url: push_calls.append((branch, repo_url)),
    )

    options = persistence.PersistenceOptions(
        input_func=lambda _: next(ctx.prompts),
        s3_client_factory=lambda region, endpoint: ctx.stub_s3(),
        pr_opener=pr_opener,
    )

    result = persistence.persist_estate(ctx.record, options)

    assert push_calls == [("estate/persist-test", str(ctx.bare))]
    assert pr_log["github_token"] == "env-token"  # noqa: S105
    assert result.pr_url == "https://example.test/pr/1"


def test_persist_estate_prefers_explicit_github_token_over_env(
    monkeypatch: pytest.MonkeyPatch,
    persist_test_context: PersistTestContext,
) -> None:
    """Explicit github_token overrides any GITHUB_TOKEN environment value."""
    ctx = persist_test_context

    monkeypatch.setenv("GITHUB_TOKEN", "env-token")

    captured_token: dict[str, str | None] = {"token": None}

    def pr_opener(context: persistence.PullRequestContext) -> str:
        captured_token["token"] = context.github_token
        return "https://example.test/pr/2"

    options = persistence.PersistenceOptions(
        input_func=lambda _: next(ctx.prompts),
        s3_client_factory=lambda region, endpoint: ctx.stub_s3(),
        pr_opener=pr_opener,
        github_token="explicit-token",  # noqa: S106
    )

    persistence.persist_estate(ctx.record, options)

    assert captured_token["token"] == "explicit-token"  # noqa: S105


def test_non_interactive_persist_uses_provided_values(
    persist_test_context: PersistTestContext,
) -> None:
    """Non-interactive mode should bypass prompts when values are provided."""
    ctx = persist_test_context
    options = persistence.PersistenceOptions(
        bucket="df12",
        region="fr-par",
        endpoint="https://s3.fr-par.scw.cloud",
        key_prefix="estates/example/main",
        key_suffix="terraform.tfstate",
        no_input=True,
        input_func=lambda _: (_ for _ in ()).throw(AssertionError("prompted")),
        s3_client_factory=lambda *_args: ctx.stub_s3(),
    )

    result = persistence.persist_estate(ctx.record, options)

    assert result.updated
    assert result.backend_path.name == "core.tfbackend"


def test_non_interactive_persist_defaults_endpoint_scheme_to_https(
    persist_test_context: PersistTestContext,
) -> None:
    """Scheme-less endpoints default to HTTPS when persisting an estate."""
    ctx = persist_test_context
    captured_endpoint: dict[str, str] = {}

    def s3_client_factory(region: str, endpoint: str) -> persistence.S3Client:
        captured_endpoint["endpoint"] = endpoint
        return ctx.stub_s3()

    options = persistence.PersistenceOptions(
        bucket="df12",
        region="fr-par",
        endpoint="s3.fr-par.scw.cloud",
        key_prefix="estates/example/main",
        key_suffix="terraform.tfstate",
        no_input=True,
        input_func=lambda _: (_ for _ in ()).throw(AssertionError("prompted")),
        s3_client_factory=s3_client_factory,
    )

    result = persistence.persist_estate(ctx.record, options)

    assert captured_endpoint["endpoint"] == "https://s3.fr-par.scw.cloud"

    backend = result.backend_path.read_text(encoding="utf-8")
    assert (
        'endpoints                   = { s3 = "https://s3.fr-par.scw.cloud" }'
        in backend
    )

    manifest = persistence.PersistenceDescriptor.from_yaml(result.manifest_path)
    assert manifest is not None
    assert manifest.endpoint == "https://s3.fr-par.scw.cloud"
