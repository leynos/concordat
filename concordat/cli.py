"""Command line entry points for the concordat tooling."""

from __future__ import annotations

import os
import sys

from cyclopts import App

from .enrol import disenrol_repositories, enrol_repositories
from .errors import ConcordatError
from .estate import (
    DEFAULT_BRANCH as ESTATE_DEFAULT_BRANCH,
)
from .estate import (
    DEFAULT_INVENTORY_PATH as ESTATE_DEFAULT_INVENTORY,
)
from .estate import (
    EstateRecord,
    get_active_estate,
    init_estate,
    list_enrolled_repositories,
    list_estates,
    set_active_estate,
)
from .estate_execution import ExecutionIO, ExecutionOptions, run_apply, run_plan
from .listing import list_namespace_repositories
from .platform_standards import PlatformStandardsConfig

app = App()


estate_app = App()

ERROR_NO_ACTIVE_ESTATE = (
    "No active estate configured. Run `concordat estate init --github-owner "
    "<owner>` followed by `concordat estate use <alias>` before enrolling "
    "repositories."
)
ERROR_ACTIVE_ESTATE_OWNER = (
    "Active estate {alias!r} is missing github_owner. Re-initialise the estate "
    "with --github-owner or update the config before enrolling repositories."
)
ERROR_NAMESPACE_REQUIRED = (
    "Specify one or more namespaces or activate an estate with "
    "`concordat estate use <alias>`."
)
ERROR_OWNER_LOOKUP_FAILED = (
    "Estate {alias!r} is missing github_owner; re-run "
    "`concordat estate init --github-owner <owner>` to record it."
)
ERROR_NO_ESTATES = "No estates configured. Run `concordat estate init` first."
ERROR_MISSING_GITHUB_TOKEN = (
    "GITHUB_TOKEN is required for concordat plan/apply; pass --github-token "  # noqa: S105
    "or export the environment variable."
)
ERROR_AUTO_APPROVE_REQUIRED = "concordat apply requires --auto-approve to continue."
ENV_SKIP_PLATFORM_PR = "CONCORDAT_SKIP_PLATFORM_PR"


def _env_flag(name: str) -> bool:
    value = os.getenv(name)
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


@app.command()
def enrol(
    *repositories: str,
    push: bool = False,
    author_name: str | None = None,
    author_email: str | None = None,
    platform_standards_url: str | None = None,
    platform_standards_branch: str = "main",
    platform_standards_inventory: str = "tofu/inventory/repositories.yaml",
    github_token: str | None = None,
) -> None:
    """Create the concordat enrolment document in each repository."""
    estate = get_active_estate()
    skip_platform_pr = _env_flag(ENV_SKIP_PLATFORM_PR)
    platform_url = None
    platform_base_branch = platform_standards_branch
    platform_inventory = platform_standards_inventory
    if not skip_platform_pr:
        platform_url = platform_standards_url or os.getenv(
            "CONCORDAT_PLATFORM_STANDARDS_URL"
        )
        if not platform_url and estate:
            platform_url = estate.repo_url
            platform_base_branch = estate.branch
            platform_inventory = estate.inventory_path

    token = github_token or os.getenv("GITHUB_TOKEN")

    if estate is None:
        raise ConcordatError(ERROR_NO_ACTIVE_ESTATE)
    owner_guard = estate.github_owner
    if not owner_guard:
        raise ConcordatError(ERROR_ACTIVE_ESTATE_OWNER.format(alias=estate.alias))

    platform_config = None
    if platform_url:
        platform_config = PlatformStandardsConfig(
            repo_url=platform_url,
            base_branch=platform_base_branch,
            inventory_path=platform_inventory,
            github_token=token,
        )

    outcomes = enrol_repositories(
        repositories,
        push_remote=push,
        author_name=author_name,
        author_email=author_email,
        platform_standards=platform_config,
        github_owner=owner_guard,
    )
    for outcome in outcomes:
        print(outcome.render())


@app.command()
def ls(*namespaces: str, token: str | None = None) -> None:
    """List SSH URLs for GitHub repositories within the given namespaces."""
    resolved_token = token or os.getenv("GITHUB_TOKEN")
    effective_namespaces = tuple(namespaces)
    if not effective_namespaces:
        estate = get_active_estate()
        if not estate:
            raise ConcordatError(ERROR_NAMESPACE_REQUIRED)
        owner = estate.github_owner
        if not owner:
            raise ConcordatError(ERROR_OWNER_LOOKUP_FAILED.format(alias=estate.alias))
        effective_namespaces = (owner,)

    urls = list_namespace_repositories(
        effective_namespaces,
        token=resolved_token,
    )
    for url in urls:
        print(url)


@app.command()
def disenrol(
    *repositories: str,
    push: bool = False,
    author_name: str | None = None,
    author_email: str | None = None,
) -> None:
    """Mark repositories as no longer enrolled in concordat."""
    outcomes = disenrol_repositories(
        repositories,
        push_remote=push,
        author_name=author_name,
        author_email=author_email,
    )
    for outcome in outcomes:
        print(outcome.render())


@estate_app.command()
def init(
    alias: str,
    repo_url: str,
    *,
    github_token: str | None = None,
    branch: str = ESTATE_DEFAULT_BRANCH,
    inventory_path: str = ESTATE_DEFAULT_INVENTORY,
    github_owner: str | None = None,
    yes: bool = False,
) -> None:
    """Initialise a platform-standards estate repository."""
    token = github_token or os.getenv("GITHUB_TOKEN")
    confirmer = (lambda _: True) if yes else None
    record = init_estate(
        alias,
        repo_url,
        branch=branch,
        inventory_path=inventory_path,
        github_owner=github_owner,
        github_token=token,
        confirm=confirmer,
    )
    print(f"initialised estate {record.alias}: {record.repo_url}")


@estate_app.command()
def use(alias: str) -> None:
    """Activate an estate so other commands can reference it."""
    record = set_active_estate(alias)
    print(f"active estate: {record.alias}")


@estate_app.command(name="ls")
def estate_ls() -> None:
    """List configured estate aliases."""
    records = list_estates()
    if not records:
        raise ConcordatError(ERROR_NO_ESTATES)
    for record in records:
        print(f"{record.alias}\t{record.repo_url}")


@estate_app.command()
def show(alias: str | None = None) -> None:
    """Show the repositories enrolled in an estate."""
    urls = list_enrolled_repositories(alias)
    for url in urls:
        print(url)


app.command(estate_app, name="estate")


def _require_active_estate() -> EstateRecord:
    record = get_active_estate()
    if record is None:
        raise ConcordatError(ERROR_NO_ACTIVE_ESTATE)
    if not record.github_owner:
        raise ConcordatError(ERROR_ACTIVE_ESTATE_OWNER.format(alias=record.alias))
    return record


def _resolve_github_token(explicit: str | None = None) -> str:
    token = explicit or os.getenv("GITHUB_TOKEN")
    if not token:
        raise ConcordatError(ERROR_MISSING_GITHUB_TOKEN)
    return token


@app.command()
def plan(
    *tofu_args: str,
    github_token: str | None = None,
    keep_workdir: bool = False,
) -> int:
    """Run `tofu plan` for the active estate."""
    record = _require_active_estate()
    token = _resolve_github_token(github_token)
    options = ExecutionOptions(
        github_owner=record.github_owner or "",
        github_token=token,
        extra_args=tofu_args,
        keep_workdir=keep_workdir,
    )
    io = ExecutionIO(stdout=sys.stdout, stderr=sys.stderr)
    exit_code, _ = run_plan(record, options, io)
    return exit_code


@app.command()
def apply(
    *tofu_args: str,
    github_token: str | None = None,
    auto_approve: bool = False,
    keep_workdir: bool = False,
) -> int:
    """Run `tofu apply` for the active estate."""
    if not auto_approve:
        raise ConcordatError(ERROR_AUTO_APPROVE_REQUIRED)
    record = _require_active_estate()
    token = _resolve_github_token(github_token)
    args = tuple(arg for arg in tofu_args if arg)
    if "-auto-approve" not in args and "-auto-approve=true" not in args:
        args = ("-auto-approve", *args)
    options = ExecutionOptions(
        github_owner=record.github_owner or "",
        github_token=token,
        extra_args=args,
        keep_workdir=keep_workdir,
    )
    io = ExecutionIO(stdout=sys.stdout, stderr=sys.stderr)
    exit_code, _ = run_apply(record, options, io)
    return exit_code


def main(argv: list[str] | tuple[str, ...] | None = None) -> int:
    """Entry point for the concordat CLI."""
    try:
        result = app(argv)
    except ConcordatError as error:
        print(f"concordat: {error}")
        return 1
    return int(result or 0)


if __name__ == "__main__":
    raise SystemExit(main())
