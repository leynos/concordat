"""Runtime checks for the guarded YAML enumeration shape.

The Rego policy accepts the guarded recipe only with `&&` between the Git
redirection and `xargs`. These tests execute the real shell recipe under
`make`, with fake `git` and linter executables on `PATH`, to show why:

* when Git fails after writing a partial listing, `&&` stops the linter from
  reading that partial list, but `;` lets it run;
* when Git succeeds, the linter's own failure still fails the target;
* the cleanup trap removes the temporary file on every exit path.

The recipes are the committed fixtures, so a change to what the policy accepts
and a change to what the shell actually does cannot drift apart silently.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import typing as typ

import pytest

_FIXTURES: typ.Final = (
    pathlib.Path(__file__).resolve().parents[2]
    / "platform-standards/canon/lint-rules/yaml-lint-baseline/fixtures/makefiles"
)
_GUARDED: typ.Final = "guarded.mk"
_SEMICOLON: typ.Final = "guarded-after-failure.mk"

_FAKE_GIT: typ.Final = """#!/bin/sh
# Emit a partial NUL-delimited listing, then report the requested status. The
# redirection has already consumed whatever was written, which is exactly the
# partial-output hazard the guarded recipe exists to contain.
printf 'alpha.yml\\0beta.yaml\\0'
exit "${FAKE_GIT_EXIT:-0}"
"""

_FAKE_LINTER: typ.Final = """#!/bin/sh
# Record the arguments xargs passes, so a test can tell whether the linter ran
# and what the failed Git left it to read.
printf '%s\\n' "$@" > "${FAKE_LINT_ARGS:?}"
exit "${FAKE_LINT_EXIT:-0}"
"""


def _write_executable(path: pathlib.Path, text: str) -> None:
    """Write *text* to *path* and make it executable."""
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


def _prepare_environment(tmp_path: pathlib.Path, makefile: str) -> dict[str, str]:
    """Return an environment running *makefile* against fake tools."""
    tools = tmp_path / "tools"
    tools.mkdir()
    _write_executable(tools / "git", _FAKE_GIT)
    linter = tools / "linter"
    _write_executable(linter, _FAKE_LINTER)
    (tmp_path / "Makefile").write_text(
        (_FIXTURES / makefile).read_text(encoding="utf-8"), encoding="utf-8"
    )
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in {"MAKEFLAGS", "MFLAGS", "MAKELEVEL"}
    }
    environment.update({
        "PATH": f"{tools}{os.pathsep}{environment.get('PATH', '')}",
        "TMPDIR": str(scratch),
        "YAMLLINT": str(linter),
        "FAKE_LINT_ARGS": str(tmp_path / "linter-args"),
        "FAKE_LINT_EXIT": "0",
        "FAKE_GIT_EXIT": "0",
    })
    return environment


def _make_executable() -> str:
    """Return the resolved `make` path, or skip when it is absent."""
    executable = shutil.which("make")
    if executable is None:
        pytest.skip("make is not installed")
    return executable


def _run(
    tmp_path: pathlib.Path,
    makefile: str,
    *,
    git_exit: int,
    lint_exit: int,
) -> subprocess.CompletedProcess[str]:
    """Run the `yamllint` target with the fake tools' exit statuses set."""
    environment = _prepare_environment(tmp_path, makefile)
    environment["FAKE_GIT_EXIT"] = str(git_exit)
    environment["FAKE_LINT_EXIT"] = str(lint_exit)
    return subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - fixed argv, resolved Make path, no shell
        [_make_executable(), "--no-print-directory", "yamllint"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


def _linter_arguments(tmp_path: pathlib.Path) -> list[str] | None:
    """Return the arguments the linter received, or None if it never ran."""
    record = tmp_path / "linter-args"
    if not record.exists():
        return None
    return record.read_text(encoding="utf-8").splitlines()


def _assert_scratch_is_clean(tmp_path: pathlib.Path) -> None:
    """Assert the cleanup trap leaves no temporary file behind."""
    assert list((tmp_path / "scratch").iterdir()) == []


_needs_tools = pytest.mark.skipif(
    any(shutil.which(tool) is None for tool in ("make", "xargs", "mktemp")),
    reason="make, xargs and mktemp are needed to run the fixture recipe",
)


@_needs_tools
def test_a_failed_git_listing_stops_the_linter(tmp_path: pathlib.Path) -> None:
    """`&&` keeps a partial enumeration away from the linter."""
    result = _run(tmp_path, _GUARDED, git_exit=1, lint_exit=0)

    assert result.returncode != 0, result
    assert _linter_arguments(tmp_path) is None, result
    _assert_scratch_is_clean(tmp_path)


@_needs_tools
def test_a_semicolon_runs_the_linter_on_partial_output(
    tmp_path: pathlib.Path,
) -> None:
    """The rejected `;` shape lints a partial listing after Git failed."""
    result = _run(tmp_path, _SEMICOLON, git_exit=1, lint_exit=0)

    assert result.returncode == 0, result
    assert _linter_arguments(tmp_path) == [
        "--strict",
        "--",
        "alpha.yml",
        "beta.yaml",
    ], result
    _assert_scratch_is_clean(tmp_path)


@_needs_tools
def test_a_failing_linter_fails_the_guarded_target(tmp_path: pathlib.Path) -> None:
    """The linter's status reaches Make through `xargs` and `&&`."""
    result = _run(tmp_path, _GUARDED, git_exit=0, lint_exit=1)

    assert result.returncode != 0, result
    assert _linter_arguments(tmp_path) == [
        "--strict",
        "--",
        "alpha.yml",
        "beta.yaml",
    ], result
    _assert_scratch_is_clean(tmp_path)


@_needs_tools
def test_a_clean_enumeration_and_lint_succeeds(tmp_path: pathlib.Path) -> None:
    """The guarded recipe passes when Git and the linter both pass."""
    result = _run(tmp_path, _GUARDED, git_exit=0, lint_exit=0)

    assert result.returncode == 0, result
    assert _linter_arguments(tmp_path) == [
        "--strict",
        "--",
        "alpha.yml",
        "beta.yaml",
    ], result
    _assert_scratch_is_clean(tmp_path)
