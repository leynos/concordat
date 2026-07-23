"""Pytest configuration marker for the script tests.

The repository root is placed on ``sys.path`` by the ``pythonpath`` setting in
``pyproject.toml`` (``[tool.pytest.ini_options]``), so these tests import the
``scripts`` package without mutating ``sys.path`` at import time.
"""

from __future__ import annotations
