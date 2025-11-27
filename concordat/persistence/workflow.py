"""Orchestration for persisting estate backend configuration."""
# ruff: noqa: TRY003

from __future__ import annotations

import importlib
import os
import typing as typ

import pygit2

from .files import _write_files_and_check_for_changes
from .inputs import _build_descriptor, _collect_user_inputs, _defaults_from
from .models import (
    BACKEND_DIRNAME,
    MANIFEST_FILENAME,
    FinalizationContext,
    PersistenceDescriptor,
    PersistenceError,
    PersistenceFiles,
    PersistenceOptions,
    PersistencePaths,
    PersistenceResult,
    PullRequestContext,
    WorkspaceContext,
)
from .pr import _build_result_message, _open_pr_if_configured
from .render import _render_tfbackend
from .validation import _validate_bucket, _validate_inputs

if typ.TYPE_CHECKING:
    from pathlib import Path

    from concordat.estate import EstateRecord


def _setup_persistence_environment(
    record: EstateRecord,
) -> tuple[WorkspaceContext, PersistencePaths]:
    """Load a clean estate workspace and derive persistence file paths."""
    workdir = _load_clean_estate(record)
    repository = pygit2.Repository(str(workdir))
    manifest_path = workdir / MANIFEST_FILENAME
    backend_path = workdir / BACKEND_DIRNAME / f"{record.alias}.tfbackend"
    return WorkspaceContext(workdir=workdir, repository=repository), PersistencePaths(
        manifest_path=manifest_path, backend_path=backend_path
    )


def _prepare_and_validate_descriptor(
    record: EstateRecord,
    paths: PersistencePaths,
    opts: PersistenceOptions,
    s3_client_factory: typ.Callable[[str, str], typ.Any],
) -> tuple[PersistenceDescriptor, str]:
    """Collect inputs, build the descriptor, and validate settings."""
    input_func = opts.input_func or input
    existing_descriptor = PersistenceDescriptor.from_yaml(paths.manifest_path)
    defaults = _defaults_from(record, existing_descriptor)
    prompts = _collect_user_inputs(defaults, input_func)
    descriptor = _build_descriptor(prompts, paths.backend_path)
    _validate_inputs(
        descriptor,
        prompts["key_suffix"],
        allow_insecure_endpoint=opts.allow_insecure_endpoint,
    )
    _validate_bucket(descriptor, prompts["key_suffix"], s3_client_factory)
    return descriptor, prompts["key_suffix"]


def _prepare_persistence_files(
    descriptor: PersistenceDescriptor,
    key_suffix: str,
    paths: PersistencePaths,
) -> PersistenceFiles:
    """Render backend contents and package persistence files."""
    backend_contents = _render_tfbackend(descriptor, key_suffix)
    manifest_contents = descriptor.to_dict()
    return PersistenceFiles(
        backend_path=paths.backend_path,
        backend_contents=backend_contents,
        manifest_path=paths.manifest_path,
        manifest_contents=manifest_contents,
    )


def _commit_and_push_changes(
    workspace: WorkspaceContext,
    paths: PersistencePaths,
    record: EstateRecord,
    opts: PersistenceOptions,
) -> str:
    """Optionally format, then commit and push persistence changes."""
    persistence_pkg = importlib.import_module("concordat.persistence")
    if opts.fmt_runner:
        opts.fmt_runner(workspace.workdir)
    branch_name = persistence_pkg._commit_changes(
        workspace.repository,
        record.branch,
        [paths.backend_path, paths.manifest_path],
        timestamp_factory=opts.timestamp_factory,
    )
    persistence_pkg._push_branch(workspace.repository, branch_name, record.repo_url)
    return branch_name


def _finalize_persistence_result(
    ctx: FinalizationContext,
    paths: PersistencePaths,
) -> PersistenceResult:
    """Open PR if configured and build final persistence result."""
    pr_context = PullRequestContext(
        record=ctx.record,
        branch_name=ctx.branch_name,
        descriptor=ctx.descriptor,
        key_suffix=ctx.key_suffix,
        github_token=ctx.github_token,
        pr_opener=ctx.opts.pr_opener,
    )
    pr_url = _open_pr_if_configured(pr_context)
    return PersistenceResult(
        backend_path=paths.backend_path,
        manifest_path=paths.manifest_path,
        branch=ctx.branch_name,
        pr_url=pr_url,
        updated=True,
        message=_build_result_message(pr_url),
    )


def persist_estate(
    record: EstateRecord,
    options: PersistenceOptions | None = None,
) -> PersistenceResult:
    """Configure remote state for an estate and open a pull request."""
    opts = options or PersistenceOptions()
    github_token = (
        opts.github_token
        if opts.github_token is not None
        else os.getenv("GITHUB_TOKEN")
    )

    workspace, paths = _setup_persistence_environment(record)

    s3_client_factory = (
        opts.s3_client_factory
        or importlib.import_module("concordat.persistence")._default_s3_client_factory
    )

    descriptor, key_suffix = _prepare_and_validate_descriptor(
        record, paths, opts, s3_client_factory
    )
    files = _prepare_persistence_files(descriptor, key_suffix, paths)

    if early_result := _write_files_and_check_for_changes(
        files,
        force=opts.force,
    ):
        return early_result

    branch_name = _commit_and_push_changes(workspace, paths, record, opts)

    finalization_ctx = FinalizationContext(
        record=record,
        branch_name=branch_name,
        descriptor=descriptor,
        key_suffix=key_suffix,
        github_token=github_token,
        opts=opts,
    )

    return _finalize_persistence_result(finalization_ctx, paths)


def _load_clean_estate(record: EstateRecord) -> Path:
    """Return the cached estate repository and ensure it is clean."""
    estate_execution = importlib.import_module("concordat.estate_execution")
    workdir = estate_execution.ensure_estate_cache(record)
    repository = pygit2.Repository(str(workdir))
    status = repository.status()
    dirty = [
        path for path, flags in status.items() if flags != pygit2.GIT_STATUS_CURRENT
    ]
    if dirty:
        formatted = ", ".join(sorted(dirty))
        raise PersistenceError(
            f"Estate cache for {record.alias!r} has uncommitted changes: {formatted}"
        )
    return workdir
