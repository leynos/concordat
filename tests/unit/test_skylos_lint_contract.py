"""Contract tests for the blocking Skylos dead-code gate."""

from __future__ import annotations

import tomllib
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).parents[2]


def test_lint_runs_a_strict_production_skylos_scan() -> None:
    """Run only production code through the reviewed Skylos dead-code gate."""
    makefile = (REPOSITORY_ROOT / "Makefile").read_text(encoding="utf-8")
    pyproject = tomllib.loads(
        (REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )

    assert "SKYLOS_VERSION = 4.33.2" in makefile
    assert "SKYLOS_PRODUCTION_TARGETS ?= concordat scripts" in makefile
    assert "--category dead_code --gate --format concise" in makefile
    assert "--no-upload --no-provenance --no-grep-verify" in makefile
    assert "skylos whitelist" in makefile
    assert (
        "tests"
        not in makefile.split("SKYLOS_PRODUCTION_TARGETS", maxsplit=1)[1].splitlines()[
            0
        ]
    )
    assert pyproject["tool"]["skylos"]["gate"]["strict"] is True
    assert pyproject["tool"]["skylos"]["whitelist"]["names"] == []
