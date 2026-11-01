"""Unit tests for the streamed Parabellum ledger index."""

from __future__ import annotations

import json
import typing as typ

from scripts import parabellum_sweep as sweep

if typ.TYPE_CHECKING:
    import pathlib


def test_report_index_does_not_retain_commit_history(tmp_path: pathlib.Path) -> None:
    """A report needs latest rows, not the sweep's complete commit index."""
    records = [
        {
            **sweep._base_record("leynos/gauss"),
            "commit_sha": sha,
            "verdict": "compliant",
        }
        for sha in ("a" * 40, "b" * 40)
    ]
    ledger_path = tmp_path / "ledger.jsonl"
    ledger_path.write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8",
    )

    index = sweep._load_ledger(
        ledger_path,
        package=sweep.RULE_PACKAGE,
        include_commit_history=False,
    )

    assert index.latest_by_repository == {"leynos/gauss": records[-1]}, index
    assert index.commits_by_repository == {}, index
