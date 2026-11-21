"""Shared checkouts for the RT-028 literal reader tests.

Each test writes the shape a repository actually uses — a workflow step that
names a nightly, a Makefile variable, a run script — and reads the facts back
through the real reader, so the fixtures live here rather than in one module.
"""

from __future__ import annotations

import textwrap
import typing as typ

from concordat.rules.toolchain import inspect_toolchain
from concordat.rules.toolchain_literal_facts import (
    ToolchainLiteralFacts,
    toolchain_literal_facts,
)

if typ.TYPE_CHECKING:
    import pathlib

MANIFEST: typ.Final = '[package]\nname = "demo"\nversion = "0.1.0"\n'
PIN: typ.Final = '[toolchain]\nchannel = "nightly-2026-11-05"\n'


def write(checkout: pathlib.Path, relative: str, text: str) -> None:
    """Write *text* at *relative* under *checkout*."""
    path = checkout / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")


def rust_repository(checkout: pathlib.Path) -> pathlib.Path:
    """Make *checkout* a Rust repository pinned to a dated nightly."""
    write(checkout, "Cargo.toml", MANIFEST)
    write(checkout, "rust-toolchain.toml", PIN)
    return checkout


def read(checkout: pathlib.Path) -> ToolchainLiteralFacts:
    """Read *checkout*'s literals against the pin facts it exposes."""
    return toolchain_literal_facts(checkout, inspect_toolchain(checkout))


def literals(facts: ToolchainLiteralFacts) -> list[tuple[str, str, str]]:
    """Return each literal as `(kind, path, text)`."""
    return [(f["kind"], f["path"], f["text"]) for f in facts["literals"]]


def texts(checkout: pathlib.Path) -> list[str]:
    """Return the text of each literal *checkout*'s files select."""
    return [text for _, _, text in literals(read(checkout))]


def workflow(steps: str) -> str:
    """Return a workflow whose single job runs *steps*."""
    return f"jobs:\n  build:\n    runs-on: ubuntu-latest\n    steps:\n{steps}"


def run_step(script: str) -> str:
    """Return one `run` step whose block holds *script* at the block's indent."""
    body = textwrap.indent(script.strip("\n"), " " * 18)
    return workflow(f"      - run: |\n{body}\n")
