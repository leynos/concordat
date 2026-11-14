"""Fixtures for the revision-list tests, registered as a pytest plugin."""

from __future__ import annotations

import typing as typ

import pytest

from tests.unit.whitaker_revisions_support import History

if typ.TYPE_CHECKING:
    import pathlib


@pytest.fixture
def history(tmp_path: pathlib.Path) -> History:
    """Return an empty synthetic shared-actions history."""
    return History(tmp_path)
