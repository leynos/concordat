#!/usr/bin/env -S uv run python
# /// script
# requires-python = ">=3.13"
# dependencies = ["cyclopts"]
# ///
"""Fail unless the pinned makeutil revision is reachable from makeutil's main.

CI installs makeutil from a commit SHA. A SHA that no branch reaches, such as a
commit from a pull-request branch that was later rebased or deleted, still
installs until GitHub garbage-collects it, and then every CI run breaks with
no change in this repository. The check fetches makeutil's main history
(commits only, no file contents) into a scratch repository and requires the
pin to be one of its ancestors, so an orphaned pin is refused when it is
introduced rather than when it disappears.

The pin and repository come from the environment, as CI sets them:

    MAKEUTIL_REVISION=<sha> uv run scripts/check_makeutil_pin.py

``MAKEUTIL_REPOSITORY`` and ``MAKEUTIL_BRANCH`` override the defaults, which is
how the tests point the check at a local repository.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import typing as typ
from pathlib import Path

import cyclopts
from cyclopts import App, Parameter

DEFAULT_REPOSITORY: typ.Final = "https://github.com/leynos/makeutil"
DEFAULT_BRANCH: typ.Final = "main"
_TRACKING_REF: typ.Final = "refs/remotes/makeutil/pinned-branch"
# A fetch of commits only is small; a git process still running after this
# long is hung on the network, and the check must fail rather than stall CI.
GIT_TIMEOUT_SECONDS: typ.Final = 120

app = App(config=cyclopts.config.Env("MAKEUTIL_", command=False))


class PinCheckError(RuntimeError):
    """The pin could not be proved reachable from the named branch."""


def _git(*arguments: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run one git command in *cwd* and capture its output.

    Returns
    -------
    subprocess.CompletedProcess[str]
        The finished process, whatever its exit status.

    Raises
    ------
    PinCheckError
        If git cannot be launched or does not finish within
        ``GIT_TIMEOUT_SECONDS``.
    """
    try:
        return subprocess.run(  # noqa: S603 - fixed git argv, no shell
            ["git", *arguments],  # noqa: S607 - git resolved from PATH
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as error:
        message = f"git {arguments[0]} timed out after {GIT_TIMEOUT_SECONDS}s"
        raise PinCheckError(message) from error
    except OSError as error:
        message = f"could not run git {arguments[0]}: {error}"
        raise PinCheckError(message) from error


def _fetch_branch_history(scratch: Path, repository: str, branch: str) -> None:
    """Fetch *branch*'s commit graph from *repository* into *scratch*.

    Raises
    ------
    PinCheckError
        If the scratch repository cannot be created or the fetch fails.
    """
    initialised = _git("init", "--quiet", cwd=scratch)
    if initialised.returncode != 0:
        message = f"could not create a scratch repository: {initialised.stderr}"
        raise PinCheckError(message)
    fetched = _git(
        "fetch",
        "--quiet",
        "--filter=tree:0",
        repository,
        f"refs/heads/{branch}:{_TRACKING_REF}",
        cwd=scratch,
    )
    if fetched.returncode != 0:
        message = f"could not fetch {branch} from {repository}: {fetched.stderr}"
        raise PinCheckError(message)


def pin_is_on_branch(revision: str, repository: str, branch: str) -> bool:
    """Report whether *revision* is an ancestor of *branch* in *repository*.

    Returns
    -------
    bool
        ``True`` when the branch's history contains the revision.

    Raises
    ------
    PinCheckError
        If the scratch directory or repository cannot be created, or git
        cannot fetch the branch; the answer is then unknown, not ``False``.

    Examples
    --------
    >>> pin_is_on_branch("6e64f4fe84419705badc30baa5649cbb6f69a298",
    ...                  DEFAULT_REPOSITORY, DEFAULT_BRANCH)  # doctest: +SKIP
    True
    """
    try:
        scratch_directory = tempfile.TemporaryDirectory(prefix="makeutil-pin-")
    except OSError as error:
        message = f"could not create a scratch directory: {error}"
        raise PinCheckError(message) from error
    with scratch_directory as scratch_name:
        scratch = Path(scratch_name)
        _fetch_branch_history(scratch, repository, branch)
        # A pin outside the branch's history is usually absent from the
        # scratch repository, which git reports as exit 128 rather than 1;
        # both mean the pin is not on the branch.
        ancestry = _git(
            "merge-base", "--is-ancestor", revision, _TRACKING_REF, cwd=scratch
        )
    return ancestry.returncode == 0


def check_pin(revision: str, repository: str, branch: str) -> None:
    """Require *revision* to be an ancestor of *branch* in *repository*.

    Raises
    ------
    PinCheckError
        If the answer cannot be obtained, or the revision is absent from or
        not an ancestor of the branch.

    Examples
    --------
    >>> check_pin("6e64f4fe84419705badc30baa5649cbb6f69a298",
    ...           DEFAULT_REPOSITORY, DEFAULT_BRANCH)  # doctest: +SKIP
    """
    if not pin_is_on_branch(revision, repository, branch):
        message = (
            f"makeutil pin {revision} is not reachable from {branch} of "
            f"{repository}; pin a commit on {branch} instead"
        )
        raise PinCheckError(message)


@app.default
def main(
    *,
    revision: typ.Annotated[str, Parameter(required=True)],
    repository: str = DEFAULT_REPOSITORY,
    branch: str = DEFAULT_BRANCH,
) -> int:
    """Check the pin and report the outcome as an exit status."""
    try:
        check_pin(revision, repository, branch)
    except PinCheckError as error:
        print(f"check_makeutil_pin: {error}", file=sys.stderr)
        return 1
    print(f"makeutil pin {revision} is on {branch} of {repository}")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised via CLI
    sys.exit(app())
