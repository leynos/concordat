"""Regenerate `baseline.json` from `baseline.toml` and `extras.toml`.

The JSON is what the `rust-lint-tables` rule and the root lint policy read, so
it is committed. Run this after editing either TOML file, from the repository
root::

    uv run python platform-standards/canon/lint-rules/rust-lint-tables/data/generate.py

A unit test builds the same document and fails when the committed JSON differs,
so a forgotten regeneration cannot reach the main branch.
"""

from __future__ import annotations

import json
from pathlib import Path

from concordat.rules.lint_baseline import baseline_document

DATA_DIR = Path(__file__).resolve().parent


def main() -> None:
    """Write `baseline.json` next to the TOML sources."""
    document = baseline_document(
        (DATA_DIR / "baseline.toml").read_text(encoding="utf-8"),
        (DATA_DIR / "extras.toml").read_text(encoding="utf-8"),
    )
    (DATA_DIR / "baseline.json").write_text(
        json.dumps(document, indent=2, sort_keys=False) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
