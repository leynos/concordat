"""Regenerate the policy-input fixture envelopes for rust-makefile-baseline.

Each ``makefiles/*.mk`` fixture is parsed with the pinned ``makeutil``
binary and wrapped in a ``policy-input/v1`` envelope under ``envelopes/``.
Two synthetic envelopes (``no_makefile`` and ``not_rust``) cover cases with
no Makefile to parse. ``data.json`` bundles every envelope under a
``fixtures`` key for ``conftest verify --data``.

Run from the rule package directory::

    python fixtures/generate.py

Exit code 2 from ``makeutil`` aborts generation; exit 1 (recovered parse)
is expected for the ``recovered`` fixture and retained.
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


def parse_makefile(path: Path) -> dict[str, object]:
    """Return the makeutil report for *path*, tolerating recovered parses."""
    # Run with a relative path so the recorded source.path stays
    # machine-independent in the checked-in envelopes.
    completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["makeutil", "parse", path.name],  # noqa: S607 - resolved from PATH
        cwd=path.parent,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if completed.returncode not in (0, 1):
        message = f"makeutil failed on {path}: {completed.stderr.strip()}"
        raise RuntimeError(message)
    report: dict[str, object] = json.loads(completed.stdout)
    return report


def build_envelope(
    *,
    makefile: dict[str, object] | None,
    root_cargo_toml: bool = True,
) -> dict[str, object]:
    """Wrap a makeutil report in a policy-input/v1 envelope."""
    return {
        "schema_version": 1,
        "kind": "policy-input/rust-makefile-baseline",
        "repository": {"path": ".", "name": None},
        "applicability": {
            "root_cargo_toml": root_cargo_toml,
            "root_makefile": makefile is not None,
        },
        "cargo": {"parsed": CARGO_PARSED} if root_cargo_toml else {"parsed": None},
        "makefile": makefile,
    }


def synthetic_envelopes() -> dict[str, dict[str, object]]:
    """Return the envelopes that have no Makefile fixture behind them."""
    return {
        "no_makefile": build_envelope(makefile=None),
        "not_rust": build_envelope(makefile=None, root_cargo_toml=False),
    }


def main() -> None:
    """Regenerate every envelope and the bundled data document."""
    ENVELOPES_DIR.mkdir(exist_ok=True)
    envelopes = synthetic_envelopes()
    for makefile_path in sorted(MAKEFILES_DIR.glob("*.mk")):
        key = makefile_path.stem.replace("-", "_")
        envelopes[key] = build_envelope(makefile=parse_makefile(makefile_path))
    for key, envelope in envelopes.items():
        target = ENVELOPES_DIR / f"{key}.json"
        target.write_text(json.dumps(envelope, indent=2) + "\n")
    bundle = FIXTURES_DIR / "data.json"
    bundle.write_text(json.dumps({"fixtures": envelopes}, indent=2) + "\n")


if __name__ == "__main__":
    main()
