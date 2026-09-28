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

from concordat.errors import OperationalRuleError
from concordat.rules import envelope as envelope_module
from concordat.rules import packages
from concordat.rules.envelope import (
    BUILD_DEFAULTS_ENVELOPE_KIND,
    build_build_defaults_envelope,
)

if typ.TYPE_CHECKING:
    import os
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


def _write_manifest(checkout: pathlib.Path) -> None:
    """Write a minimal root `Cargo.toml` so the checkout is a Rust surface."""
    (checkout / "Cargo.toml").write_text(
        '[package]\nname = "x"\nversion = "0.1.0"\n', encoding="utf-8"
    )


class TestEnvelopeContents:
    """The builder reports what it read, and says so when it read nothing."""

    def test_an_unreadable_root_cargo_probe_raises(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A probe failure cannot become no Rust applicability here either.

        The explicit empty `language.rust.surfaces` list makes the surface
        resolver authoritative, so it returns without reading the manifest
        and this builder's own probe is the only reader of it. Without the
        declaration the resolver would raise afterwards and the assertion
        would hold however the probe behaved.
        """
        cargo_path = tmp_path / "Cargo.toml"
        cargo_path.write_text(
            '[package]\nname = "x"\nversion = "0.1.0"\n', encoding="utf-8"
        )
        (tmp_path / ".concordat").write_text(
            "language:\n  rust:\n    surfaces: []\n", encoding="utf-8"
        )
        original_stat = pathlib.Path.stat

        def unreadable_cargo(
            path: pathlib.Path, *, follow_symlinks: bool = True
        ) -> os.stat_result:
            """Raise the filesystem error only for the root Cargo probe."""
            if path == cargo_path:
                raise PermissionError
            return original_stat(path, follow_symlinks=follow_symlinks)

        monkeypatch.setattr(pathlib.Path, "stat", unreadable_cargo)

        with pytest.raises(OperationalRuleError, match="cannot inspect") as exc_info:
            build_build_defaults_envelope(tmp_path)

        error = exc_info.value
        assert error.operation == "resolve-rust-surfaces", error.operation
        assert error.resource == cargo_path, error.resource

    def test_a_bare_checkout_reports_every_absence(
        self, tmp_path: pathlib.Path
    ) -> None:
        """An absent file is a fact in its own right, not a missing key."""
        (tmp_path / "Cargo.toml").write_text(
            '[package]\nname = "x"\nversion = "0.1.0"\n', encoding="utf-8"
        )
        envelope = build_build_defaults_envelope(tmp_path)
        assert envelope["kind"] == BUILD_DEFAULTS_ENVELOPE_KIND, (
            f"the envelope must name its own kind, got {envelope['kind']!r}"
        )
        assert envelope["cargo_config"] is None, "no configuration was written"
        assert envelope["toolchain"] is None, "no toolchain was pinned"
        assert envelope["applicability"]["cargo_config"] is False, (
            "applicability must mirror the absent configuration"
        )
        assert envelope["applicability"]["toolchain_file"] is False, (
            "applicability must mirror the absent toolchain pin"
        )

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
        paths = [scan["path"] for scan in scans]
        assert paths == ["docs/adr-029.md"], (
            f"only the declared document is scanned, got {paths!r}"
        )
        assert len(scans[0]["sections"]) == 1, (
            "the declared keyword must be the one matched"
        )

    def test_no_makefile_and_no_workflows_are_absences(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A checkout with neither carries empty facts, not errors."""
        _write_manifest(tmp_path)
        envelope = build_build_defaults_envelope(tmp_path)
        assert envelope["makefile"] is None, "no Makefile was written"
        assert envelope["makefile_error"] is None, "nothing was refused"
        assert envelope["workflows"] == [], "no workflow was written"


class TestBuildPathFacts:
    """The Makefile and workflows BD-007 to BD-009 read.

    An assigned `RUSTFLAGS` replaces what Cargo auto-discovers, and a coverage
    build cannot use a Cranelift default, so the envelope carries the files
    that make those builds as well as the ones Cargo reads.
    """

    def test_the_makefile_report_is_carried(self, tmp_path: pathlib.Path) -> None:
        """The pinned `makeutil` report is the Makefile fact, unaltered."""
        _write_manifest(tmp_path)
        (tmp_path / "Makefile").write_text(
            "coverage:\n\tcargo llvm-cov --lcov\n", encoding="utf-8"
        )
        envelope = build_build_defaults_envelope(tmp_path)
        report = envelope["makefile"]
        assert report is not None, "the Makefile must be parsed"
        assert [rule["targets"] for rule in report["rules"]] == [["coverage"]], report[
            "rules"
        ]
        assert envelope["makefile_error"] is None, "makeutil accepted the file"

    def test_a_refused_makefile_is_carried_as_its_reason(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A refusal decides only the clauses that read the Makefile.

        BD-001 to BD-006 never read it, so raising would make them unrunnable
        against a checkout whose Makefile `makeutil` cannot parse.
        """
        _write_manifest(tmp_path)
        (tmp_path / "Makefile").write_text("all:\n", encoding="utf-8")

        def refuse(path: pathlib.Path) -> typ.NoReturn:
            message = f"makeutil exited with status 2 for {path.name}"
            raise OperationalRuleError(
                message, operation="parse-makefile", resource=path
            )

        monkeypatch.setattr(envelope_module, "inspect_makefile", refuse)
        envelope = build_build_defaults_envelope(tmp_path)
        assert envelope["makefile"] is None, "a refused Makefile has no report"
        assert envelope["makefile_error"] == (
            "makeutil exited with status 2 for Makefile"
        ), envelope["makefile_error"]
        assert envelope["kind"] == BUILD_DEFAULTS_ENVELOPE_KIND, (
            "the rest of the envelope is still built"
        )

    def test_a_makefile_linked_outside_the_checkout_is_refused(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A link could carry another tree's Makefile into the audit."""
        outside = tmp_path / "outside" / "Makefile"
        outside.parent.mkdir()
        outside.write_text("all:\n", encoding="utf-8")
        checkout = tmp_path / "checkout"
        checkout.mkdir()
        _write_manifest(checkout)
        (checkout / "Makefile").symlink_to(outside)
        with pytest.raises(OperationalRuleError, match="outside the checkout"):
            build_build_defaults_envelope(checkout)

    def test_workflows_are_carried_decoded(self, tmp_path: pathlib.Path) -> None:
        """Each workflow is decoded YAML, or its error, never raw text."""
        _write_manifest(tmp_path)
        workflows = tmp_path / ".github" / "workflows"
        workflows.mkdir(parents=True)
        (workflows / "ci.yml").write_text(
            "on: pull_request\njobs:\n  test:\n    runs-on: ubuntu-latest\n",
            encoding="utf-8",
        )
        (workflows / "broken.yml").write_text("jobs: [\n", encoding="utf-8")
        facts = {
            workflow["path"]: workflow
            for workflow in build_build_defaults_envelope(tmp_path)["workflows"]
        }
        ci = facts[".github/workflows/ci.yml"]
        assert ci["error"] is None, ci
        assert isinstance(ci["parsed"], dict), ci
        parsed = typ.cast("dict[str, object]", ci["parsed"])
        assert parsed["on"] == "pull_request", "YAML 1.2 keeps `on` a string"
        assert facts[".github/workflows/broken.yml"]["error"] is not None, (
            "an undecodable workflow keeps its reason"
        )


class TestBuilderSelection:
    """A package opts into its own facts; every other package is untouched."""

    def test_the_build_defaults_package_takes_its_own_builder(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Selecting the wrong builder would send the policy the wrong facts."""
        (tmp_path / "Cargo.toml").write_text(
            '[package]\nname = "x"\nversion = "0.1.0"\n', encoding="utf-8"
        )
        envelope = packages.default_envelope_builder("rust-build-defaults", tmp_path)
        assert envelope["kind"] == BUILD_DEFAULTS_ENVELOPE_KIND, (
            f"the package's own builder must be chosen, got {envelope['kind']!r}"
        )

    def test_an_unregistered_package_keeps_the_makefile_envelope(
        self, tmp_path: pathlib.Path
    ) -> None:
        """The historic builder stays the default for everything unnamed."""
        (tmp_path / "Cargo.toml").write_text(
            '[package]\nname = "x"\nversion = "0.1.0"\n', encoding="utf-8"
        )
        envelope = packages.default_envelope_builder("rust-makefile-baseline", tmp_path)
        assert envelope["kind"] == "policy-input/rust-makefile-baseline", (
            f"an unregistered package keeps the historic envelope, "
            f"got {envelope['kind']!r}"
        )


def _comparable(fixtures: dict[str, dict[str, object]]) -> dict[str, object]:
    """Return *fixtures* with every parse diagnostic reduced to its presence.

    A `parse_error` holds `str(tomllib.TOMLDecodeError)`, whose wording belongs
    to the interpreter. Comparing it verbatim would fail a checked-in fixture
    on a Python release that reworded the diagnostic, even though the
    configuration is as unparsable as it ever was. The policy asks only
    whether the field is set, so that is what is compared; the real message
    stays in the generated envelopes for whoever reads one.

    Returns
    -------
    dict[str, object]
        A copy of *fixtures* with each parse diagnostic replaced by a marker.
    """
    reduced = json.loads(json.dumps(fixtures))
    for envelope in reduced.values():
        config = envelope.get("cargo_config")
        if isinstance(config, dict) and config.get("parse_error") is not None:
            config["parse_error"] = "<set>"
    return typ.cast("dict[str, object]", reduced)


@pytest.fixture(scope="module")
def generated() -> dict[str, dict[str, object]]:
    """Return a fresh generation of every fixture envelope.

    Returns
    -------
    dict[str, dict[str, object]]
        The envelopes the generator produces from the checked-in fixtures.
    """
    generator = _load_generator()
    built = generator.build_envelopes()
    return built | generator.synthetic_envelopes(built)


class TestCheckedInFixtures:
    """The fixtures the Rego suite verifies against must be current."""

    def test_the_bundle_matches_a_fresh_generation(
        self, generated: dict[str, dict[str, object]]
    ) -> None:
        """A stale bundle tests the policy against facts it no longer receives."""
        bundle = json.loads(
            (RULE_DIR / "fixtures" / "data.json").read_text(encoding="utf-8")
        )
        assert _comparable(bundle["fixtures"]) == _comparable(generated), (
            "regenerate the fixtures: `uv run python "
            "platform-standards/canon/lint-rules/rust-build-defaults/"
            "fixtures/generate.py`"
        )

    def test_every_envelope_file_matches_the_bundle(
        self, generated: dict[str, dict[str, object]]
    ) -> None:
        """The per-fixture files and the bundle are one set, not two."""
        envelopes_dir = RULE_DIR / "fixtures" / "envelopes"
        on_disk = {
            path.stem: json.loads(path.read_text(encoding="utf-8"))
            for path in envelopes_dir.glob("*.json")
        }
        assert _comparable(on_disk) == _comparable(generated), (
            "the per-fixture files and the bundle must be regenerated together"
        )
