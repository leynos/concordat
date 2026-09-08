"""Regenerate the policy-input fixture envelopes for rust-makefile-baseline.

Each ``makefiles/*.mk`` fixture is parsed with the pinned ``makeutil``
binary and wrapped in a ``policy-input/v1`` envelope under ``envelopes/``.
Two synthetic envelopes (``no_makefile`` and ``not_rust``) cover cases with
no Makefile to parse. ``data.json`` bundles every envelope under a
``fixtures`` key for ``conftest verify --data``.

Run from the rule package directory::

    python fixtures/generate.py

Any makeutil failure aborts generation with a ``MakeutilFixtureError``;
exit 1 (recovered parse) is accepted only for the ``recovered`` fixture.
"""

from __future__ import annotations

import json
import subprocess
import typing as typ
from pathlib import Path

FIXTURES_DIR = Path(__file__).resolve().parent
MAKEFILES_DIR = FIXTURES_DIR / "makefiles"
ENVELOPES_DIR = FIXTURES_DIR / "envelopes"

CARGO_PARSED: typ.Final = {"package": {"name": "fixture", "version": "0.1.0"}}
ROOT_SURFACE: typ.Final = {
    "path": "Cargo.toml",
    "role": "crate",
    "parsed": CARGO_PARSED,
}
NESTED_SURFACE: typ.Final = {
    "path": "rust/Cargo.toml",
    "role": "workspace",
    "parsed": {"workspace": {"members": []}},
}

# Only this fixture is expected to parse with recovery (makeutil exit 1).
RECOVERED_FIXTURE: typ.Final = "recovered"
MIXED_SURFACE_FIXTURES: typ.Final = frozenset({"mixed_root_nested"})


class MakeutilFixtureError(RuntimeError):
    """A makeutil invocation failed while regenerating fixtures.

    Mirrors the ``OperationalRuleError`` pattern used in
    ``concordat/rules/envelope.py``: a named domain error that carries the
    offending tool and resource instead of a bare ``RuntimeError``.
    """

    def __init__(self, path: Path, detail: str) -> None:
        """Initialise with the Makefile path and makeutil's diagnostic."""
        super().__init__(f"makeutil failed on {path}: {detail}")
        self.tool = "makeutil"
        self.resource = path


def _validate_returncode(returncode: int, path: Path, stderr: str) -> None:
    """Accept exit 0 for any fixture and exit 1 only for the recovered one."""
    if returncode == 0:
        return
    if returncode == 1 and path.stem == RECOVERED_FIXTURE:
        return
    raise MakeutilFixtureError(path, stderr.strip())


def parse_makefile(path: Path) -> dict[str, object]:
    """Return the makeutil report for *path*, tolerating recovered parses."""
    # Run with a relative path so the recorded source.path stays
    # machine-independent in the checked-in envelopes.
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
            ["makeutil", "parse", path.name],  # noqa: S607 - resolved from PATH
            cwd=path.parent,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        # `OSError` covers the absent binary (`FileNotFoundError`) alongside
        # every other launch failure, such as a `makeutil` on PATH that is not
        # executable, so none escapes as a raw operating-system error.
        detail = f"could not run makeutil: {error}"
        raise MakeutilFixtureError(path, detail) from error
    _validate_returncode(completed.returncode, path, completed.stderr)
    report: dict[str, object] = json.loads(completed.stdout)
    return report


def build_envelope(
    *,
    makefile: dict[str, object] | None,
    root_cargo_toml: bool = True,
    surfaces: list[dict[str, object]] | None = None,
    rust_surfaces_declared: bool = False,
) -> dict[str, object]:
    """Wrap a makeutil report in a policy-input/v1 envelope."""
    resolved_surfaces = surfaces
    if resolved_surfaces is None:
        resolved_surfaces = [ROOT_SURFACE] if root_cargo_toml else []
    return {
        "schema_version": 1,
        "kind": "policy-input/rust-makefile-baseline",
        "repository": {"path": ".", "name": None},
        "applicability": {
            "root_cargo_toml": root_cargo_toml,
            "root_makefile": makefile is not None,
            "rust_surfaces_declared": rust_surfaces_declared,
        },
        "cargo": {
            "parsed": CARGO_PARSED if root_cargo_toml else None,
            "surfaces": resolved_surfaces,
        },
        "makefile": makefile,
    }


def synthetic_envelopes() -> dict[str, dict[str, object]]:
    """Return the envelopes that have no Makefile fixture behind them."""
    return {
        "no_makefile": build_envelope(makefile=None),
        "not_rust": build_envelope(makefile=None, root_cargo_toml=False),
        "declared_empty": build_envelope(
            makefile=None,
            root_cargo_toml=False,
            surfaces=[],
            rust_surfaces_declared=True,
        ),
    }


def main() -> None:
    """Regenerate every envelope and the bundled data document."""
    ENVELOPES_DIR.mkdir(exist_ok=True)
    envelopes = synthetic_envelopes()
    for makefile_path in sorted(MAKEFILES_DIR.glob("*.mk")):
        key = makefile_path.stem.replace("-", "_")
        surfaces = None
        rust_surfaces_declared = False
        if key in MIXED_SURFACE_FIXTURES:
            surfaces = [ROOT_SURFACE, NESTED_SURFACE]
            rust_surfaces_declared = True
        elif key.startswith("surface_"):
            surfaces = [NESTED_SURFACE]
            rust_surfaces_declared = True
        envelopes[key] = build_envelope(
            makefile=parse_makefile(makefile_path),
            root_cargo_toml=surfaces is None,
            surfaces=surfaces,
            rust_surfaces_declared=rust_surfaces_declared,
        )
    for key, envelope in envelopes.items():
        target = ENVELOPES_DIR / f"{key}.json"
        target.write_text(json.dumps(envelope, indent=2) + "\n")
    bundle = FIXTURES_DIR / "data.json"
    bundle.write_text(json.dumps({"fixtures": envelopes}, indent=2) + "\n")


if __name__ == "__main__":
    main()
