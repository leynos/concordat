"""Decide whether a workflow's `run` lines stop at the first failure.

A multi-line GitHub Actions `run` block is one script under one shell, and
whether a later line is reached after a failed one depends entirely on that
shell. `bash -e {0}` — the default on Linux and macOS — stops. `pwsh`, the
Windows default, checks only the last native exit code. `cmd` and `python`
never stop. A custom template such as `bash {0}` spells out the flags, and
without `-e` it does not stop either.

The distinction matters here because a `cargo fetch --locked` on one line only
protects a test on the next line if a failed fetch prevents the test from
running. Reading any newline as proof would report a fetch-protected entry
point on a runner where the test still runs against a cold cache.

Resolution follows GitHub's own precedence: the step's `shell`, the job's
`defaults.run.shell`, the workflow's `defaults.run.shell`, and finally the
default the `runs-on` label implies. Where none of those can be established —
an expression, or a runner label this reader does not recognise — the answer
says so, and the caller reports `uncertain` rather than guessing.
"""

from __future__ import annotations

import dataclasses
import re
import shlex
import types
import typing as typ

if typ.TYPE_CHECKING:
    import collections.abc as cabc

_EXPRESSION: typ.Final = re.compile(r"\$\{\{.*?\}\}", re.DOTALL)


@dataclasses.dataclass(frozen=True, slots=True)
class ShellRule:
    """What a workflow declares about the shell a `run` step's lines share.

    ``fail_fast`` says the shell stops at the first failing line, so a later
    line is reached only when the earlier one succeeded. ``is_known`` says the
    declaration could be read at all.
    """

    fail_fast: bool
    is_known: bool


UNKNOWN_SHELL: typ.Final = ShellRule(fail_fast=False, is_known=False)
# GitHub's built-in shell keywords. Only `bash` and `sh` are run with `-e`;
# `pwsh` checks the last native exit code, and `python` and `cmd` do not stop.
_BUILTIN_SHELLS: typ.Final = types.MappingProxyType({
    "bash": True,
    "cmd": False,
    "powershell": False,
    "pwsh": False,
    "python": False,
    "sh": True,
})
# A custom shell template is fail-fast only when it asks a POSIX shell for
# errexit; `bash {0}` runs every line whatever happened.
_POSIX_SHELLS: typ.Final = frozenset({"bash", "dash", "ksh", "sh", "zsh"})
_WINDOWS_LABEL: typ.Final = "windows"
_POSIX_RUNNER_LABELS: typ.Final = frozenset({"linux", "macos", "ubuntu"})


def _mapping(value: object) -> dict[str, object] | None:
    """Return *value* as a string-keyed mapping, or ``None`` when it is not one.

    Returns
    -------
    dict[str, object] | None
        *value* when it is a mapping, otherwise ``None``.
    """
    return typ.cast("dict[str, object]", value) if isinstance(value, dict) else None


def _runner_rule(runs_on: object) -> ShellRule:
    """Return the default shell a `runs-on` value implies.

    GitHub's non-Windows default is `bash -e {0}`, which stops at the first
    failing line; the Windows default is `pwsh`, which does not. A value that
    is an expression or names no recognisable platform cannot be resolved.

    Returns
    -------
    ShellRule
        Whether the runner's default shell fails fast, and whether that could
        be established.
    """
    match runs_on:
        case str():
            if not runs_on.strip() or _EXPRESSION.search(runs_on):
                return UNKNOWN_SHELL
            return ShellRule(
                fail_fast=not runs_on.strip().lower().startswith(_WINDOWS_LABEL),
                is_known=True,
            )
        case list():
            return _label_rule(runs_on)
    return UNKNOWN_SHELL


def _label_rule(labels: cabc.Sequence[object]) -> ShellRule:
    """Return the default shell a `runs-on` label list implies.

    A list naming a Windows label gets `pwsh`; one naming a Linux or macOS
    family gets the POSIX default. Labels the reader cannot classify, and
    labels hidden behind an expression, leave the answer unknown.

    Returns
    -------
    ShellRule
        The labels' default shell, or an unknown rule.
    """
    named = [item for item in labels if isinstance(item, str)]
    if len(named) != len(labels) or any(_EXPRESSION.search(item) for item in named):
        return UNKNOWN_SHELL
    lowered = {item.strip().lower() for item in named}
    if any(item.startswith(_WINDOWS_LABEL) for item in lowered):
        return ShellRule(fail_fast=False, is_known=True)
    if any(item.split("-", 1)[0] in _POSIX_RUNNER_LABELS for item in lowered):
        return ShellRule(fail_fast=True, is_known=True)
    return UNKNOWN_SHELL


def _requests_errexit(words: list[str]) -> bool:
    """Report whether a shell command line asks for errexit.

    `-e`, `-eu` and `-eo pipefail` all do; `-c` does not, and a long option is
    never errexit under any of these shells.

    Returns
    -------
    bool
        True when an option letter `e` is requested.
    """
    return any(
        word.startswith("-") and not word.startswith("--") and "e" in word[1:]
        for word in words
    )


def _shell_rule(value: object) -> ShellRule:
    """Return what one explicit `shell:` value proves about its lines.

    Returns
    -------
    ShellRule
        Whether the shell stops at the first failing line, and whether the
        value could be read.
    """
    if not isinstance(value, str):
        return UNKNOWN_SHELL
    text = value.strip()
    if not text or _EXPRESSION.search(text):
        return UNKNOWN_SHELL
    try:
        words = shlex.split(text)
    except ValueError:
        return UNKNOWN_SHELL
    if not words:
        return UNKNOWN_SHELL
    name = words[0].rsplit("/", 1)[-1]
    built_in = _BUILTIN_SHELLS.get(name)
    if len(words) == 1 and built_in is not None:
        return ShellRule(fail_fast=built_in, is_known=True)
    if "{0}" not in words:
        # GitHub requires a custom shell to name the script it runs; without
        # `{0}` this is not a template this reader can interpret.
        return UNKNOWN_SHELL
    if name in _POSIX_SHELLS:
        return ShellRule(fail_fast=_requests_errexit(words[1:]), is_known=True)
    if built_in is not None:
        return ShellRule(fail_fast=False, is_known=True)
    return UNKNOWN_SHELL


def _default_shell(block: object) -> object | None:
    """Return a `defaults.run.shell` value from a workflow or job mapping.

    Returns
    -------
    object | None
        The declared shell, or ``None`` when the block declares none.
    """
    outer = _mapping(block)
    if outer is None:
        return None
    defaults = _mapping(outer.get("defaults"))
    if defaults is None:
        return None
    run = _mapping(defaults.get("run"))
    return None if run is None else run.get("shell")


def effective_shell(
    step: cabc.Mapping[str, object],
    job: cabc.Mapping[str, object],
    workflow: object,
    runs_on: object,
) -> ShellRule:
    """Return the shell a `run` step's lines actually share.

    The most specific declaration wins, exactly as GitHub resolves it: the
    step, then the job's defaults, then the workflow's defaults, then the
    runner's own default shell.

    Parameters
    ----------
    step:
        The step mapping, which may name its own `shell`.
    job:
        The job mapping the step belongs to.
    workflow:
        The workflow's top-level `defaults` block, if it declares one.
    runs_on:
        The job's `runs-on` value, which selects the default shell.

    Returns
    -------
    ShellRule
        The shell's line-sequencing behaviour, or an unknown rule.
    """
    for value in (
        step.get("shell"),
        _default_shell(job),
        _default_shell(workflow),
    ):
        if value is not None:
            return _shell_rule(value)
    return _runner_rule(runs_on)
