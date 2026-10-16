"""Recognize the canonical uv helper's documentation-launcher contract.

This pure adapter owns Python operands, the canonical helper path, and its
selected argument grammar. The shell reader supplies the existing Make unknown
marker and consumes either the original argument shape or a refusal; it keeps
all shell parsing, recipe provenance and audit-fact construction. Nothing here
reads or imports a helper from an audited checkout.
"""

from __future__ import annotations

import itertools
import pathlib
import re
import typing as typ

GATE_HELPER: typ.Final = "scripts/uv_gate.py"


def python_arguments(operands: list[str], unresolved_mark: str) -> list[str] | None:
    """Read supported Python operands without interpreting shell words.

    Parameters
    ----------
    operands
        The words after the recognized Python executable, before interrogate.
    unresolved_mark
        The caller's marker for an unexpanded Make value.

    Returns
    -------
    list[str] | None
        Launcher arguments unchanged, or ``None`` for a refused Python form.
    """
    script_path = pathlib.PurePosixPath(operands[0] if operands else "")
    if ".." in script_path.parts:
        return None
    if script_path == pathlib.PurePosixPath(GATE_HELPER):
        return _gate_helper_arguments(operands[1:], unresolved_mark)
    if operands != ["-m"]:
        return None
    return operands


# Private recognition grammar from the selected uv_gate.py.canon test fixture;
# this is not the broader UV policy's release-tag exception mechanism.
_GATE_PIN_NAME: typ.Final = r"[A-Za-z0-9][A-Za-z0-9._-]*(?:\[[^\]]+\])?"
_GATE_NAME: typ.Final = re.compile(_GATE_PIN_NAME)
_GATE_VERSION: typ.Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+!-]*")
_GATE_DIRECT_REFERENCE: typ.Final = re.compile(
    rf"{_GATE_PIN_NAME}\s*@\s*(?P<url>git\+\S+)"
)
_GATE_FULL_SHA: typ.Final = re.compile(r"[0-9a-f]{40}")
# Match validate_request/_refuse_forbidden in that same trusted fixture.
# This is a static grammar: consumer helper code must never be imported.
_GATE_FORBIDDEN_FLAGS: typ.Final = frozenset({
    "--no-offline",
    "--no-cache",
    "--no-frozen",
    "--no-locked",
    "--locked",
    "--frozen",
    "--offline",
})
_GATE_FORBIDDEN_PREFIXES: typ.Final = ("--refresh", "--upgrade", "--reinstall")
_GATE_FORBIDDEN_SHORT: typ.Final = frozenset("UPn")
_GATE_FORBIDDEN_SHORT_PREFIXES: typ.Final = tuple(
    f"-{flag}" for flag in sorted(_GATE_FORBIDDEN_SHORT)
)


def _gate_short_flag_is_forbidden(name: str) -> bool:
    """Mirror the canon's short-alias and alphabetic-cluster refusal."""
    letters = name[1:]
    if letters.isalpha():
        return name.startswith("-") and not _GATE_FORBIDDEN_SHORT.isdisjoint(letters)
    return name.startswith(_GATE_FORBIDDEN_SHORT_PREFIXES)


def _gate_flag_is_forbidden(argument: str) -> bool:
    """Recognize canonical policy overrides, including equals-value forms."""
    name = argument.partition("=")[0]
    return (
        name in _GATE_FORBIDDEN_FLAGS
        or name.startswith(_GATE_FORBIDDEN_PREFIXES)
        or _gate_short_flag_is_forbidden(name)
    )


def _gate_spec_is_pinned(spec: str) -> bool:
    """Recognize only the selected canonical helper's accepted pin syntax."""
    direct = _GATE_DIRECT_REFERENCE.fullmatch(spec)
    if direct is not None:
        spec = direct.group("url")
    if spec.startswith("git+"):
        ref = spec.rpartition("@")[2].partition("#")[0]
        return _GATE_FULL_SHA.fullmatch(ref) is not None
    return any(_gate_exact_pin(spec, separator) for separator in ("==", "@"))


def _gate_exact_pin(spec: str, separator: str) -> bool:
    """Preserve first-separator semantics, including separators inside extras."""
    name, found, version = spec.partition(separator)
    if not found or version == "latest":
        return False
    return bool(_GATE_NAME.fullmatch(name) and _GATE_VERSION.fullmatch(version))


def _gate_tool_spec(arguments: list[str]) -> str:
    """Read the helper's first --from value before its first separator.

    With no explicit specification the command is `interrogate`, which the
    helper refuses as unpinned, so the empty result is also rejected.

    Returns
    -------
    str
        The first explicit specification, or empty when absent or missing.
    """
    options = itertools.takewhile(lambda word: word != "--", arguments[1:])
    for argument in options:
        if argument == "--from":
            return next(options, "")
        if argument.startswith("--from="):
            return argument.partition("=")[2]
    return ""


def _gate_tool_arguments_are_valid(arguments: list[str], unresolved_mark: str) -> bool:
    """Accept an exact pin or retain an unexpanded tool specification."""
    spec = _gate_tool_spec(arguments)
    return unresolved_mark in spec or _gate_spec_is_pinned(spec)


def _gate_run_arguments_are_valid(arguments: list[str]) -> bool:
    """Require one final command separator for the helper's run prefix."""
    return arguments.count("--") == 1 and arguments[-1:] == ["--"]


def _gate_helper_arguments(
    arguments: list[str], unresolved_mark: str
) -> list[str] | None:
    """Return canonical helper arguments, or ``None`` for an invalid run boundary.

    Only `python_arguments` calls this reader after recognizing the canonical
    helper path; its caller has already recognized the Python interpreter.
    The helper `tool` form additionally
    requires a specification accepted by its canonical pin recognizer. Both
    modes refuse the canonical helper's forbidden pre-separator flags.

    Returns
    -------
    list[str] | None
        The arguments unchanged, or ``None`` for a refused helper boundary.
    """
    if arguments[:1] not in (["run"], ["tool"]):
        return arguments
    options = itertools.takewhile(lambda word: word != "--", arguments[1:])
    if any(_gate_flag_is_forbidden(option) for option in options):
        return None
    valid = (
        _gate_tool_arguments_are_valid(arguments, unresolved_mark)
        if arguments[:1] == ["tool"]
        else _gate_run_arguments_are_valid(arguments)
    )
    return arguments if valid else None


def is_unresolved_tool_prefix(words: list[str], unresolved_mark: str) -> bool:
    """Recognize the existing helper-specific unknown-spec recovery boundary.

    Parameters
    ----------
    words
        Command words after the shell reader removes environment prefixes.
    unresolved_mark
        The caller's marker for an unexpanded Make value.

    Returns
    -------
    bool
        Whether the caller should retain an unresolved helper invocation.
    """
    if len(words) < 2:
        return False
    if pathlib.PurePosixPath(words[1]) != pathlib.PurePosixPath(GATE_HELPER):
        return False
    if words[0].endswith(("/", "/.")):
        return False
    if pathlib.PurePosixPath(words[0]).name not in {"python", "python3"}:
        return False
    arguments = python_arguments(words[1:], unresolved_mark)
    if arguments is None or arguments[:1] != ["tool"]:
        return False
    return unresolved_mark in _gate_tool_spec(arguments)
