r"""Inspect and extend QG-002's certification of `install-whitaker` revisions.

QG-002 certifies a shared-actions revision when it descends from a certification
tag (`certified/install-whitaker/<installer version>`) with the action's runtime
content unchanged since it. The audit asks the GitHub API; these commands work
on a shared-actions clone and change nothing in concordat.

List what the tags in a clone certify::

    uv run python scripts/whitaker_revisions.py list ../shared-actions

Check a reviewed commit and print the commands that certify it. The commands
are printed, never run, and name the clone with `git -C`, so they work from any
directory: creating and pushing a tag is the maintainer's act::

    uv run python scripts/whitaker_revisions.py certify ../shared-actions \
        <commit> --installer-version 0.2.12

`list` prints each floor and then every first-parent commit up to the tip that
a floor certifies, oldest first. The derivation lives in
`concordat.rules.whitaker_revisions`.
"""

from __future__ import annotations

import re
import shlex
import sys
import typing as typ
from pathlib import Path  # noqa: TC003  # cyclopts reads the annotation at runtime.

import pygit2
from cyclopts import App

from concordat.errors import OperationalRuleError
from concordat.rules import packages
from concordat.rules.whitaker_certification import DEFAULT_TAG_PREFIX
from concordat.rules.whitaker_revisions import (
    certified_revisions,
    certified_tags,
    floor_digest,
)

RULE_ID: typ.Final = "whitaker-provisioning"
OPERATION: typ.Final = "derive-whitaker-revisions"
_VERSION: typ.Final = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+([-+][0-9A-Za-z.-]+)?$")

app = App(help=__doc__)


def _manifest_string(parameters: dict[str, object], key: str, default: str) -> str:
    """Return the rule manifest's string parameter *key*, or *default* if unset."""
    value = parameters.get(key)
    return value if isinstance(value, str) and value else default


def _parameters() -> tuple[str, str]:
    """Return the tag prefix and the action directory the rule manifest names."""
    parameters = packages.rule_parameters(packages.rule_package_dir(RULE_ID))
    return (
        _manifest_string(parameters, "certification_tag_prefix", DEFAULT_TAG_PREFIX),
        _manifest_string(
            parameters, "action_directory", ".github/actions/install-whitaker"
        ),
    )


def _open(clone: Path) -> pygit2.Repository:
    """Open *clone*, or refuse as an operational failure.

    Returns
    -------
    pygit2.Repository
        The open repository.

    Raises
    ------
    OperationalRuleError
        When *clone* is not a Git repository.
    """
    try:
        return pygit2.Repository(str(clone))
    except pygit2.GitError as error:
        message = f"{clone} is not a Git repository: {error}"
        raise OperationalRuleError(
            message, operation=OPERATION, resource=clone
        ) from error


@app.command(name="list")
def list_revisions(clone: Path, tip: str = "origin/main") -> int:
    """Print each certification floor, then the revisions the floors certify."""
    prefix, directory = _parameters()
    repository = _open(clone)
    for tag, commit in certified_tags(repository, prefix).items():
        print(f"floor {tag} {commit}")
    for revision in certified_revisions(repository, prefix, directory, tip):
        print(f"certified {revision}")
    return 0


def _require_on_tip(repository: pygit2.Repository, commit: str, tip: str) -> str:
    """Return the full id of *commit*, which must be on the first-parent line of *tip*.

    Returns
    -------
    str
        The full commit id.

    Raises
    ------
    OperationalRuleError
        When *commit* or *tip* is unreadable, or *commit* is not on *tip*'s
        first-parent history.
    """
    try:
        found = repository.revparse_single(commit).peel(pygit2.Commit)
        tip_id = repository.revparse_single(tip).peel(pygit2.Commit).id
        walker = repository.walk(tip_id)
        walker.simplify_first_parent()
        on_tip = any(step.id == found.id for step in walker)
    except (pygit2.GitError, KeyError, ValueError) as error:
        message = f"cannot read {commit} or {tip} in the clone: {error}"
        raise OperationalRuleError(message, operation=OPERATION) from error
    if not on_tip:
        message = f"{commit} is not on the first-parent history of {tip}"
        raise OperationalRuleError(message, operation=OPERATION)
    return str(found.id)


@app.command
def certify(
    clone: Path,
    commit: str,
    *,
    installer_version: str,
    tip: str = "origin/main",
) -> int:
    """Check a reviewed commit and print the commands that certify it.

    The commit must be on the first-parent history of *tip*, hold the action
    directory, and have runtime files that name no path the digest ignores. The
    tag is not created here: the maintainer runs the printed commands.

    Returns
    -------
    int
        0 after printing the commands.

    Raises
    ------
    OperationalRuleError
        When the version is malformed or already tagged, or the commit is
        unreadable, off *tip*, or unsafe to certify.
    """
    prefix, directory = _parameters()
    if not _VERSION.fullmatch(installer_version):
        message = f"{installer_version!r} is not a whitaker-installer version"
        raise OperationalRuleError(message, operation=OPERATION)
    repository = _open(clone)
    sha = _require_on_tip(repository, commit, tip)
    digest = floor_digest(repository, sha, directory)
    tag = f"{prefix}{installer_version}"
    if tag in certified_tags(repository, prefix):
        message = f"the clone already has the tag {tag}; certify a new version"
        raise OperationalRuleError(message, operation=OPERATION)
    # The commands name the clone, so they work from any directory, including a
    # concordat checkout that holds none of shared-actions' commits.
    git = f"git -C {shlex.quote(str(clone))}"
    print(f"runtime digest {digest}")
    print(f'{git} tag -a {tag} {sha} -m "Certify install-whitaker {installer_version}"')
    print(f"{git} push origin {tag}")
    return 0


def main() -> int:
    """Run the command line and return its exit status."""
    try:
        return typ.cast("int", app(sys.argv[1:], result_action="return_value"))
    except OperationalRuleError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
