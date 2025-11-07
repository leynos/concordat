"""Tests for priority model loading."""

from __future__ import annotations

from pathlib import Path

from concordat.auditor.priority import load_priority_model


def test_load_priority_model_defaults_when_missing(tmp_path: Path) -> None:
    """Fallback to the built-in priority model when file is absent."""
    path = tmp_path / "missing.yaml"
    model = load_priority_model(path)
    assert model.labels[0].name == "priority/p0-blocker"
    assert model.schema_version == 1


def test_load_priority_model_from_repo() -> None:
    """Parse the repository's canonical priority model."""
    path = Path("platform-standards/canon/priorities/priority-model.yaml")
    model = load_priority_model(path)
    assert len(model.labels) == 4
    assert model.labels[1].name == "priority/p1-critical"
