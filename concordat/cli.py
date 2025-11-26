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
    EstateNotConfiguredError,
    EstateRecord,
    get_active_estate,
    get_estate,
    init_estate,
    list_enrolled_repositories,
    list_estates,
    set_active_estate,
)
from .estate_execution import ExecutionIO, ExecutionOptions, run_apply, run_plan
from .listing import list_namespace_repositories
from .persistence import PersistenceOptions, persist_estate
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
    "GITHUB_TOKEN is required for concordat plan/apply; "  # noqa: S105
    "pass --github-token or export the environment variable."
)
ERROR_AUTO_APPROVE_REQUIRED = "concordat apply requires --auto-approve to continue."
ENV_SKIP_PLATFORM_PR = "CONCORDAT_SKIP_PLATFORM_PR"


def _env_flag(name: str) -> bool:
    value = os.getenv(name)
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _resolve_platform_config(
    estate: EstateRecord | None,
    explicit_url: str | None,
    branch: str,
    inventory: str,
    token: str | None,
) -> PlatformStandardsConfig | None:
    """Return the platform-standards config if PR automation should run."""
    if _env_flag(ENV_SKIP_PLATFORM_PR):
        return None

    platform_url = explicit_url or os.getenv("CONCORDAT_PLATFORM_STANDARDS_URL")
    base_branch = branch
    inventory_path = inventory
    branch_is_default = not branch or branch == ESTATE_DEFAULT_BRANCH
    inventory_is_default = not inventory or inventory == ESTATE_DEFAULT_INVENTORY
    if not platform_url and estate is not None:
        platform_url = estate.repo_url
        if branch_is_default:
            base_branch = estate.branch
        if inventory_is_default:
            inventory_path = estate.inventory_path

    if not platform_url:
        return None

    return PlatformStandardsConfig(
        repo_url=platform_url,
        base_branch=base_branch,
        inventory_path=inventory_path,
        github_token=token,
    )


def _ensure_auto_approve_flag(args: tuple[str, ...]) -> tuple[str, ...]:
    """Ensure -auto-approve is the first argument when not already present."""
    filtered = tuple(arg for arg in args if arg)
    lowered = {arg.lower() for arg in filtered}
    if "-auto-approve" not in lowered and "-auto-approve=true" not in lowered:
        return ("-auto-approve", *filtered)
    return filtered


def _resolve_namespaces(namespaces: tuple[str, ...]) -> tuple[str, ...]:
    """Return namespaces or fall back to the active estate owner."""
    if namespaces:
        return namespaces
    if (estate := get_active_estate()) is None:
        raise ConcordatError(ERROR_NAMESPACE_REQUIRED)
    if owner := estate.github_owner:
        return (owner,)
    raise ConcordatError(ERROR_OWNER_LOOKUP_FAILED.format(alias=estate.alias))


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
    estate = _require_active_estate()
    token = github_token or os.getenv("GITHUB_TOKEN")

    owner_guard = estate.github_owner
    if not owner_guard:
        raise ConcordatError(ERROR_ACTIVE_ESTATE_OWNER.format(alias=estate.alias))

    platform_config = _resolve_platform_config(
        estate=estate,
        explicit_url=platform_standards_url,
        branch=platform_standards_branch,
        inventory=platform_standards_inventory,
        token=token,
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
    effective_namespaces = _resolve_namespaces(tuple(namespaces))

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


@estate_app.command()
def persist(
    alias: str | None = None,
    *,
    force: bool = False,
    github_token: str | None = None,
    allow_insecure_endpoint: bool = False,
) -> None:
    """Configure remote state persistence for an estate."""
    record = _resolve_estate_record(alias)
    token = github_token or os.getenv("GITHUB_TOKEN")
    options = PersistenceOptions(
        force=force,
        github_token=token,
        allow_insecure_endpoint=allow_insecure_endpoint,
    )
    result = persist_estate(record, options)
    print(result.render())


app.command(estate_app, name="estate")


def _require_active_estate() -> EstateRecord:
    if (record := get_active_estate()) is None:
        raise ConcordatError(ERROR_NO_ACTIVE_ESTATE)
    if not record.github_owner:
        raise ConcordatError(ERROR_ACTIVE_ESTATE_OWNER.format(alias=record.alias))
    return record


def _resolve_estate_record(alias: str | None) -> EstateRecord:
    if alias:
        record = get_estate(alias)
        if record is None:
            raise EstateNotConfiguredError(alias)
        if not record.github_owner:
            raise ConcordatError(ERROR_ACTIVE_ESTATE_OWNER.format(alias=record.alias))
    else:
        record = _require_active_estate()
    return record


def _resolve_github_token(explicit: str | None = None) -> str:
    if not (token := explicit or os.getenv("GITHUB_TOKEN")):
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
    args = _ensure_auto_approve_flag(tuple(tofu_args))
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
