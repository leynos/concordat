"""Unit tests for the rust-build-defaults policy input.

The envelope decides what the policy is allowed to ask about. These tests pin
the facts it carries, that its fixture envelopes are the ones the production
builder produces, and that a rule package can choose its own builder without
disturbing the package that predates the choice.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import typing as typ

import pytest

from concordat.rules import runner
from concordat.rules.envelope import (
    BUILD_DEFAULTS_ENVELOPE_KIND,
    build_build_defaults_envelope,
)

if typ.TYPE_CHECKING:
    import types

RULE_DIR: typ.Final = (
    pathlib.Path(__file__).resolve().parents[2]
    / "platform-standards"
    / "canon"
    / "lint-rules"
    / "rust-build-defaults"
)


def _load_generator() -> types.ModuleType:
    """Import the rule package's `fixtures/generate.py` by path.

    Returns
    -------
    types.ModuleType
        The loaded generator module.

    Raises
    ------
    ImportError
        If a module spec cannot be built from the generator's path.
    """
    path = RULE_DIR / "fixtures" / "generate.py"
    spec = importlib.util.spec_from_file_location("rust_build_defaults_generate", path)
    if spec is None or spec.loader is None:
        message = f"could not load a module spec from {path}"
        raise ImportError(message)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestEnvelopeContents:
    """The builder reports what it read, and says so when it read nothing."""

    def test_a_bare_checkout_reports_every_absence(
        self, tmp_path: pathlib.Path
    ) -> None:
        """An absent file is a fact in its own right, not a missing key."""
        (tmp_path / "Cargo.toml").write_text(
            '[package]\nname = "x"\nversion = "0.1.0"\n', encoding="utf-8"
        )
        envelope = build_build_defaults_envelope(tmp_path)
        assert envelope["kind"] == BUILD_DEFAULTS_ENVELOPE_KIND
        assert envelope["cargo_config"] is None
        assert envelope["toolchain"] is None
        assert envelope["applicability"]["cargo_config"] is False
        assert envelope["applicability"]["toolchain_file"] is False

    def test_the_declared_documents_are_the_ones_scanned(
        self, tmp_path: pathlib.Path
    ) -> None:
        """The document list is a rule parameter, not a fixed path."""
        (tmp_path / "Cargo.toml").write_text(
            '[package]\nname = "x"\nversion = "0.1.0"\n', encoding="utf-8"
        )
        adr = tmp_path / "docs" / "adr-029.md"
        adr.parent.mkdir()
        adr.write_text("# ADR\n\n## Backend\n\nOn `nightly`.\n", encoding="utf-8")
        envelope = build_build_defaults_envelope(
            tmp_path,
            {
                "exception_documents": ["docs/adr-029.md"],
                "exception_keyword": "Backend",
            },
        )
        scans = envelope["exceptions"]
        assert [scan["path"] for scan in scans] == ["docs/adr-029.md"]
        assert len(scans[0]["sections"]) == 1

    def test_no_makefile_facts_are_carried(self, tmp_path: pathlib.Path) -> None:
        """The clauses read files Cargo discovers, so a Makefile is irrelevant.

        Deliberate rather than incidental: reading it would make the rule
        unrunnable against any checkout the pinned Makefile parser rejects,
        and would add no fact this policy decides anything from.
        """
        (tmp_path / "Cargo.toml").write_text(
            '[package]\nname = "x"\nversion = "0.1.0"\n', encoding="utf-8"
        )
        (tmp_path / "Makefile").write_text(
            "export NOT_AN_ASSIGNMENT\n", encoding="utf-8"
        )
        envelope = build_build_defaults_envelope(tmp_path)
        assert "makefile" not in envelope


class TestBuilderSelection:
    """A package opts into its own facts; every other package is untouched."""

    def test_the_build_defaults_package_takes_its_own_builder(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Selecting the wrong builder would send the policy the wrong facts."""
        (tmp_path / "Cargo.toml").write_text(
            '[package]\nname = "x"\nversion = "0.1.0"\n', encoding="utf-8"
        )
        envelope = runner._envelope_for("rust-build-defaults", tmp_path)
        assert envelope["kind"] == BUILD_DEFAULTS_ENVELOPE_KIND

    def test_an_unregistered_package_keeps_the_makefile_envelope(
        self, tmp_path: pathlib.Path
    ) -> None:
        """The historic builder stays the default for everything unnamed."""
        (tmp_path / "Cargo.toml").write_text(
            '[package]\nname = "x"\nversion = "0.1.0"\n', encoding="utf-8"
        )
        envelope = runner._envelope_for("rust-makefile-baseline", tmp_path)
        assert envelope["kind"] == "policy-input/rust-makefile-baseline"


@pytest.fixture(scope="module")
def generated() -> dict[str, dict[str, object]]:
    """Return a fresh generation of every fixture envelope.

    Returns
    -------
    dict[str, dict[str, object]]
        The envelopes the generator produces from the checked-in fixtures.
    """
    generator = _load_generator()
    return generator.build_envelopes() | generator.synthetic_envelopes()


class TestCheckedInFixtures:
    """The fixtures the Rego suite verifies against must be current."""

    def test_the_bundle_matches_a_fresh_generation(
        self, generated: dict[str, dict[str, object]]
    ) -> None:
        """A stale bundle tests the policy against facts it no longer receives."""
        bundle = json.loads(
            (RULE_DIR / "fixtures" / "data.json").read_text(encoding="utf-8")
        )
        assert bundle["fixtures"] == generated

    def test_every_envelope_file_matches_the_bundle(
        self, generated: dict[str, dict[str, object]]
    ) -> None:
        """The per-fixture files and the bundle are one set, not two."""
        envelopes_dir = RULE_DIR / "fixtures" / "envelopes"
        on_disk = {
            path.stem: json.loads(path.read_text(encoding="utf-8"))
            for path in envelopes_dir.glob("*.json")
        }
        assert on_disk == generated
