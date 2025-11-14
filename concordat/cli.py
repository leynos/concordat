"""Command line entry points for the concordat tooling."""

from __future__ import annotations

import os

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
    get_active_estate,
    init_estate,
    list_enrolled_repositories,
    list_estates,
    set_active_estate,
)
from .listing import list_namespace_repositories
from .platform_standards import PlatformStandardsConfig

app = App()
estate_app = App(name="estate", help="Manage estates registered with concordat")


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
    platform_url = platform_standards_url or os.getenv(
        "CONCORDAT_PLATFORM_STANDARDS_URL"
    )
    token = github_token or os.getenv("GITHUB_TOKEN")
    if not platform_url:
        estate = get_active_estate()
        if estate:
            platform_url = estate.repo_url
            platform_standards_branch = estate.branch
            platform_standards_inventory = estate.inventory_path

    platform_config = None
    if platform_url:
        platform_config = PlatformStandardsConfig(
            repo_url=platform_url,
            base_branch=platform_standards_branch,
            inventory_path=platform_standards_inventory,
            github_token=token,
        )

    outcomes = enrol_repositories(
        repositories,
        push_remote=push,
        author_name=author_name,
        author_email=author_email,
        platform_standards=platform_config,
    )
    for outcome in outcomes:
        print(outcome.render())


@app.command()
def ls(*namespaces: str, token: str | None = None) -> None:
    """List SSH URLs for GitHub repositories within the given namespaces."""
    resolved_token = token or os.getenv("GITHUB_TOKEN")
    urls = list_namespace_repositories(
        namespaces,
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
        github_token=token,
        confirm=confirmer,
    )
    print(f"initialised estate {record.alias}: {record.repo_url}")


@estate_app.command()
def use(alias: str) -> None:
    """Activate an estate so other commands can reference it."""
    record = set_active_estate(alias)
    print(f"active estate: {record.alias}")


@estate_app.command()
def ls() -> None:
    """List configured estate aliases."""
    records = list_estates()
    if not records:
        raise ConcordatError(
            "No estates configured. Run `concordat estate init` first."
        )
    for record in records:
        print(f"{record.alias}\t{record.repo_url}")


@estate_app.command()
def show(alias: str | None = None) -> None:
    """Show the repositories enrolled in an estate."""
    urls = list_enrolled_repositories(alias)
    for url in urls:
        print(url)


app.command(estate_app, name="estate")


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
