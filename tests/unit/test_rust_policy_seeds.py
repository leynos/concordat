"""Keep the Rust consumer seeds valid and reachable through the canon."""

from __future__ import annotations

import re
from pathlib import Path

import ruamel.yaml

ROOT = Path(__file__).resolve().parents[2]
CANON = ROOT / "platform-standards" / "canon"
YAML = ruamel.yaml.YAML(typ="safe")


def test_rust_policy_seeds_are_registered() -> None:
    """A consumer cannot select a seed omitted from the manifest."""
    manifest = YAML.load((CANON / "manifest.yaml").read_text(encoding="utf-8"))
    registered = {entry["id"]: entry["path"] for entry in manifest["artifacts"]}

    assert registered["rust-clippy-config"] == (
        "platform-standards/canon/lint/rust/clippy.toml"
    )
    assert registered["rust-dependabot-template"] == (
        "platform-standards/canon/dependabot/rust-dependabot.yml"
    )
    assert registered["repository-label-model"] == (
        "platform-standards/canon/labels/repository-labels.yaml"
    )


def test_repository_labels_respect_github_metadata_limits() -> None:
    """API reconciliation must not fail on malformed label metadata."""
    model = YAML.load(
        (CANON / "labels" / "repository-labels.yaml").read_text(encoding="utf-8")
    )
    labels = model["labels"]
    names = [label["name"] for label in labels]

    assert model["schema_version"] == 1
    assert all(isinstance(name, str) and 0 < len(name) <= 50 for name in names)
    assert len(names) == len(set(names))
    assert all(re.fullmatch(r"[0-9a-fA-F]{6}", label["color"]) for label in labels)
    assert all(
        label["description"] is None or len(label["description"]) <= 100
        for label in labels
    )


def test_dependabot_template_uses_modelled_labels() -> None:
    """A copied seed must not refer to labels outside the published model."""
    model = YAML.load(
        (CANON / "labels" / "repository-labels.yaml").read_text(encoding="utf-8")
    )
    template = YAML.load(
        (CANON / "dependabot" / "rust-dependabot.yml").read_text(encoding="utf-8")
    )
    known_labels = {label["name"] for label in model["labels"]}
    updates = template["updates"]

    assert template["version"] == 2
    assert {entry["package-ecosystem"] for entry in updates} == {
        "cargo",
        "github-actions",
        "rust-toolchain",
    }
    assert all(set(entry["labels"]) <= known_labels for entry in updates)
