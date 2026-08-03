"""Workflow-level tests for persistence operations."""

from __future__ import annotations

import dataclasses
import typing as typ

import pytest

import concordat.persistence.gitops as gitops
import concordat.persistence.validation as persistence_validation
import concordat.persistence.workflow as persistence_workflow
from concordat import estate_execution, persistence, xdg
from concordat.estate import ActiveOwnerMismatchError, EstateRecord
from tests.unit.conftest import PersistTestContext, _make_repo

if typ.TYPE_CHECKING:
    from pathlib import Path


def _write_owner_token(owner: str, token: str) -> None:
    """Write *owner*'s credentials file with the mode the loader demands."""
    path = xdg.owner_credentials_path(owner)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"GITHUB_TOKEN: {token}\n", encoding="utf-8")
    path.chmod(0o600)


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


class TestOwnerScopedCredentials:
    """Persistence resolves credentials for the estate's own owner.

    The active owner is a shell-level convenience; the estate's
    ``github_owner`` is the identity the work belongs to. Resolving tokens or
    object-storage keys from the former would push one owner's estate with
    another owner's credentials.
    """

    def test_record_owner_selects_the_github_token(
        self,
        monkeypatch: pytest.MonkeyPatch,
        persist_test_context: PersistTestContext,
    ) -> None:
        """The token comes from the record's owner, not the active owner.

        No active owner is configured here on purpose. A mismatched active
        owner is refused outright (covered below), so this is the case that
        separates the two: resolving from the active owner would find nothing
        and pass a token of ``None`` to the pull-request opener, while
        resolving from the record finds ``bravo``'s.
        """
        _write_owner_token("alpha", "alpha-token")
        _write_owner_token("bravo", "bravo-token")
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        assert xdg.get_active_owner() is None, "this case needs no active owner"

        ctx = persist_test_context
        record = dataclasses.replace(ctx.record, github_owner="bravo")
        seen: dict[str, str | None] = {}

        def pr_opener(context: persistence.PullRequestContext) -> str:
            seen["token"] = context.github_token
            return "https://example.test/pr/3"

        monkeypatch.setattr(gitops, "_push_branch", lambda *_args: None)
        persistence.persist_estate(
            record,
            persistence.PersistenceOptions(
                input_func=lambda _: next(ctx.prompts),
                s3_client_factory=lambda region, endpoint: ctx.stub_s3(),
                pr_opener=pr_opener,
            ),
        )

        assert seen["token"] == "bravo-token", (  # noqa: S105
            f"the record owner's token should be used, got {seen['token']!r}"
        )

    def test_default_s3_factory_is_bound_to_the_record_owner(
        self,
        monkeypatch: pytest.MonkeyPatch,
        persist_test_context: PersistTestContext,
    ) -> None:
        """With no injected factory, the default one is bound to the owner."""
        ctx = persist_test_context
        record = dataclasses.replace(ctx.record, github_owner="bravo")
        seen: dict[str, object] = {}

        # `owner` is defaulted so an unbound factory reports `None` here rather
        # than raising: the assertion below then names what actually happened.
        def fake_factory(
            region: str,
            endpoint: str,
            *,
            owner: str | None = None,
        ) -> object:
            seen["owner"] = owner
            return ctx.stub_s3()

        monkeypatch.setattr(
            persistence_validation, "_default_s3_client_factory", fake_factory
        )
        monkeypatch.setattr(gitops, "_push_branch", lambda *_args: None)

        persistence.persist_estate(
            record,
            persistence.PersistenceOptions(
                input_func=lambda _: next(ctx.prompts),
            ),
        )

        assert seen["owner"] == "bravo", (
            f"the default S3 factory should be scoped to the record owner, got {seen!r}"
        )

    def test_an_injected_factory_still_receives_only_region_and_endpoint(
        self,
        monkeypatch: pytest.MonkeyPatch,
        persist_test_context: PersistTestContext,
    ) -> None:
        """The public two-argument factory contract is unchanged.

        `owner` is bound onto the *default* factory only. An injected one is
        called exactly as before, so no caller has to grow a parameter.
        """
        ctx = persist_test_context
        record = dataclasses.replace(ctx.record, github_owner="bravo")
        calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

        # Deliberately variadic: the point is to record exactly how the
        # factory was called, which a two-parameter signature could not do —
        # an unexpected keyword would raise before anything was recorded. The
        # cast restates the contract the assertions below then check at run
        # time.
        def factory(*args: object, **kwargs: object) -> persistence.S3Client:
            calls.append((args, kwargs))
            return typ.cast("persistence.S3Client", ctx.stub_s3())

        monkeypatch.setattr(gitops, "_push_branch", lambda *_args: None)
        persistence.persist_estate(
            record,
            persistence.PersistenceOptions(
                input_func=lambda _: next(ctx.prompts),
                s3_client_factory=typ.cast(
                    "typ.Callable[[str, str], persistence.S3Client]", factory
                ),
            ),
        )

        assert calls, "the injected factory should have been called"
        for args, kwargs in calls:
            assert len(args) == 2, f"expected (region, endpoint), got {args!r}"
            assert not kwargs, f"an injected factory takes no keywords, got {kwargs!r}"

    def test_a_mismatched_active_owner_is_refused_before_any_work(
        self,
        monkeypatch: pytest.MonkeyPatch,
        persist_test_context: PersistTestContext,
    ) -> None:
        """An estate is never persisted while a different owner is active.

        The refusal has to come first: once the cache is loaded or a client is
        built, credentials have already been read and a remote contacted.
        """
        _write_owner_token("alpha", "alpha-token")
        _write_owner_token("bravo", "bravo-token")
        xdg.set_active_owner("alpha")

        ctx = persist_test_context
        record = dataclasses.replace(ctx.record, github_owner="bravo")

        def forbidden(name: str) -> typ.Callable[..., typ.NoReturn]:
            def refuse(*_args: object, **_kwargs: object) -> typ.NoReturn:
                message = f"{name} ran after an owner mismatch"
                raise AssertionError(message)

            return refuse

        monkeypatch.setattr(
            estate_execution, "ensure_estate_cache", forbidden("cache loading")
        )
        monkeypatch.setattr(
            persistence_validation,
            "_default_s3_client_factory",
            forbidden("S3 client creation"),
        )
        monkeypatch.setattr(gitops, "_push_branch", forbidden("the git push"))

        with pytest.raises(ActiveOwnerMismatchError, match="bravo") as info:
            persistence.persist_estate(
                record,
                persistence.PersistenceOptions(
                    input_func=forbidden("prompting"),
                    s3_client_factory=forbidden("the injected S3 factory"),
                    pr_opener=forbidden("opening a pull request"),
                ),
            )

        assert info.value.active_owner == "alpha", info.value.active_owner
        assert info.value.estate_owner == "bravo", info.value.estate_owner

    def test_a_matching_active_owner_is_permitted(
        self,
        monkeypatch: pytest.MonkeyPatch,
        persist_test_context: PersistTestContext,
    ) -> None:
        """The guard fires on a mismatch only, not on any active owner."""
        ctx = persist_test_context
        xdg.set_active_owner(ctx.record.github_owner or "example")
        monkeypatch.setattr(gitops, "_push_branch", lambda *_args: None)

        result = persistence.persist_estate(
            ctx.record,
            persistence.PersistenceOptions(
                input_func=lambda _: next(ctx.prompts),
                s3_client_factory=lambda region, endpoint: ctx.stub_s3(),
            ),
        )

        assert result.updated, result


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
