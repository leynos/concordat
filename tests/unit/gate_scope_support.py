"""Shared builders for the gate-workspace-coverage envelope tests."""

from __future__ import annotations

import typing as typ

from concordat.rules.gate_scope_commands import invocations_in
from concordat.rules.gate_scope_envelope import build_gate_scope_envelope
from concordat.rules.interrogate_invocation import lex_segments

if typ.TYPE_CHECKING:
    import pathlib

    from concordat.rules.gate_scope_commands import Invocation
    from concordat.rules.gate_scope_envelope import GateScopeEnvelope

ROOT_PACKAGE = (
    '[package]\nname = "app"\nversion = "0.1.0"\n\n'
    '[workspace]\nmembers = ["crates/*"]\n'
)
MEMBER = '[package]\nname = "{name}"\nversion = "0.1.0"\n'


def lay_out(checkout: pathlib.Path, files: dict[str, str]) -> None:
    """Write *files*, creating directories."""
    for relative, text in files.items():
        path = checkout / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def workspace_files(*, makefile: str = "", extra: dict[str, str] | None = None) -> dict:
    """Return a root-package workspace with members `b` and `c`."""
    files = {
        "Cargo.toml": ROOT_PACKAGE,
        "crates/b/Cargo.toml": MEMBER.format(name="b"),
        "crates/c/Cargo.toml": MEMBER.format(name="c"),
    }
    if makefile:
        files["Makefile"] = makefile
    return {**files, **(extra or {})}


def build(tmp_path: pathlib.Path, files: dict[str, str]) -> GateScopeEnvelope:
    """Lay out a checkout and return its envelope."""
    lay_out(tmp_path, files)
    return build_gate_scope_envelope(tmp_path)


def read(command: str) -> list[Invocation]:
    """Return the invocations in one shell command line."""
    segments = lex_segments(command)
    assert segments is not None, f"the lexer must accept {command!r}"
    return invocations_in(segments, ("test", command))


def selection(command: str) -> tuple[str, bool, list[str], list[str]]:
    """Return `(tool, workspace_flag, packages, excludes)` of the only invocation."""
    (found,) = read(command)
    return found["tool"], found["workspace_flag"], found["packages"], found["excludes"]
