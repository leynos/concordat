#!/usr/bin/env -S uv run python
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""CLI entry point for syncing project acronyms into the Vale allow list."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path


def main() -> int:
    """Sync custom acronyms into the downloaded Concordat style."""
    acronym_allowlist = importlib.import_module("concordat_vale.acronym_allowlist")
    repo_root = Path(__file__).resolve().parents[1]
    source = repo_root / ".config" / "common-acronyms"
    target = (
        repo_root / ".vale" / "styles" / "config" / "scripts" / "AcronymsFirstUse.tengo"
    )

    try:
        acronyms = acronym_allowlist.load_project_acronyms(source)
        result = acronym_allowlist.update_allow_map(target, acronyms)
    except (FileNotFoundError, acronym_allowlist.AcronymAllowlistError) as exc:
        print(exc, file=sys.stderr)
        return 1

    if result.managed_entries:
        verb = "Updated" if result.wrote_file else "Already present"
        count = len(result.managed_entries)
        rel_target = target.relative_to(repo_root)
        print(f"{verb} {count} acronyms in {rel_target}.")
    else:
        print("No project-specific acronyms to inject.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
