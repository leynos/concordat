"""Command line entry points for the concordat tooling."""

from __future__ import annotations

import asyncio
import os

from cyclopts import App

from .enrol import enrol_repositories
from .errors import ConcordatError
from .listing import list_namespace_repositories

app = App()


@app.command()
def enrol(
    *repositories: str,
    push: bool = False,
    author_name: str | None = None,
    author_email: str | None = None,
) -> None:
    """Create the concordat enrolment document in each repository."""
    outcomes = enrol_repositories(
        repositories,
        push_remote=push,
        author_name=author_name,
        author_email=author_email,
    )
    for outcome in outcomes:
        print(outcome.render())


@app.command()
def ls(*namespaces: str, token: str | None = None) -> None:
    """List SSH URLs for GitHub repositories within the given namespaces."""
    resolved_token = token or os.getenv("GITHUB_TOKEN")
    urls = asyncio.run(
        list_namespace_repositories(
            namespaces,
            token=resolved_token,
        )
    )
    for url in urls:
        print(url)


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
