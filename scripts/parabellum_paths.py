"""Repository-relative default paths for the Parabellum sweep.

Split out so the manifest, ledger, and report modules can each name their
own default without importing the sweep driver they are imported *by*.
"""

from __future__ import annotations

import pathlib
import typing as typ

REPO_ROOT: typ.Final = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_ESTATE_PATH: typ.Final = REPO_ROOT / "docs" / "parabellum" / "estate.yaml"
DEFAULT_LEDGER_PATH: typ.Final = REPO_ROOT / "docs" / "parabellum" / "ledger.jsonl"
DEFAULT_REPORT_PATH: typ.Final = (
    REPO_ROOT / "docs" / "parabellum" / "baseline-report.md"
)
