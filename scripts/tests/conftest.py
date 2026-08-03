"""Shared fixtures for the Parabellum script tests.

The repository root is placed on ``sys.path`` by the ``pythonpath`` setting in
``pyproject.toml`` (``[tool.pytest.ini_options]``), so these tests import the
``scripts`` package without mutating ``sys.path`` at import time.

``estate_path`` and ``ledger_path`` live here because four of the Parabellum
test modules need them; helpers used by a single module stay in that module.
"""

from __future__ import annotations

import typing as typ

import pytest

if typ.TYPE_CHECKING:
    import pathlib

ESTATE_YAML = """\
---
schema_version: 1
owner: leynos
repositories:
  - name: wireframe
  - name: gauss
    excluded: test-framework migration in flight
  - name: statelet
"""


@pytest.fixture
def estate_path(tmp_path: pathlib.Path) -> pathlib.Path:
    """Write a small estate inventory and return its path."""
    path = tmp_path / "estate.yaml"
    path.write_text(ESTATE_YAML)
    return path


@pytest.fixture
def ledger_path(tmp_path: pathlib.Path) -> pathlib.Path:
    """Return a ledger path inside the test's temporary directory."""
    return tmp_path / "ledger.jsonl"
