"""The envelope resolver refuses a package it cannot choose an input for.

A rule package reads facts of one shape and its policy expects that shape.
When the two disagree the policy does not degrade: it answers confidently
about a document it was never written for, and the operator sees a verdict
rather than a failure. The resolver therefore has no fallback, and these
tests pin both halves of that — the packages it does resolve, and the refusal
for everything else.
"""

from __future__ import annotations

import typing as typ

import pytest

from concordat.errors import OperationalRuleError
from concordat.rules import packages, runner
from concordat.rules.envelope import (
    BUILD_DEFAULTS_ENVELOPE_KIND,
    ENVELOPE_KIND,
)

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    import pathlib

CARGO: typ.Final = '[package]\nname = "fixture"\nversion = "0.1.0"\n'


@pytest.fixture
def checkout(tmp_path: pathlib.Path) -> pathlib.Path:
    """Return a minimal Rust checkout the resolver can build an envelope for."""
    (tmp_path / "Cargo.toml").write_text(CARGO, encoding="utf-8")
    return tmp_path


class TestRegisteredPackages:
    """Every shipped package resolves, and to the envelope its policy expects."""

    @pytest.mark.parametrize(
        ("rule_id", "kind"),
        [
            pytest.param("rust-makefile-baseline", ENVELOPE_KIND, id="makefile"),
            pytest.param(
                "rust-build-defaults", BUILD_DEFAULTS_ENVELOPE_KIND, id="build-defaults"
            ),
        ],
    )
    def test_the_package_resolves_to_its_own_envelope(
        self, checkout: pathlib.Path, rule_id: str, kind: str
    ) -> None:
        """Resolving to the other package's envelope is the defect guarded here."""
        envelope = packages.default_envelope_builder(rule_id, checkout)
        assert envelope["kind"] == kind, (
            f"{rule_id} should be audited over {kind}, got {envelope['kind']!r}"
        )

    def test_every_shipped_package_is_registered(self) -> None:
        """The mapping is the complete list, not the exceptions to a default.

        Without this, adding a package and forgetting to register it is caught
        only when someone runs it, and this suite would pass over the gap.
        """
        shipped = {
            path.name
            for path in packages._rule_packages_dir().iterdir()
            if (path / "policy").is_dir()
        }
        assert shipped, "the discovery must find the shipped packages"
        unregistered = shipped - set(packages.PACKAGE_ENVELOPE_BUILDERS)
        assert unregistered == set(), (
            f"these packages have no envelope builder: {sorted(unregistered)}"
        )

    def test_every_registered_builder_produces_a_known_input_kind(self) -> None:
        """The two mappings describe the same set of builders."""
        by_id = set(packages.PACKAGE_ENVELOPE_BUILDERS.values())
        by_kind = set(packages.INPUT_KIND_ENVELOPE_BUILDERS.values())
        assert by_id == by_kind, (
            "a builder reachable by identifier but not by declared kind, or the "
            "reverse, makes the two routes disagree about what a package gets"
        )


class TestUnresolvablePackages:
    """A package the resolver cannot choose an input for is refused."""

    def test_an_unregistered_identifier_is_refused(
        self, checkout: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The gap this module exists for: it used to take the Makefile envelope.

        `rust-build-defaults` reads no Makefile and its policy refuses an
        envelope of the other kind outright, so the silent fallback turned a
        registration mistake into an EN-001 finding about the wrong document.
        """
        monkeypatch.setattr(packages, "PACKAGE_ENVELOPE_BUILDERS", {})
        monkeypatch.setattr(packages, "_declared_input_kind", lambda _rule_dir: None)
        with pytest.raises(OperationalRuleError) as excinfo:
            packages.default_envelope_builder("rust-build-defaults", checkout)
        message = str(excinfo.value)
        assert "rust-build-defaults" in message, (
            f"the refusal must name the package, got {message!r}"
        )
        assert excinfo.value.operation == "select-policy-envelope", (
            "the failure needs a stable operation identifier for automation"
        )

    def test_the_refusal_names_what_would_have_worked(
        self, checkout: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An error that says only "no" leaves the reader to grep for the fix."""
        monkeypatch.setattr(
            packages,
            "PACKAGE_ENVELOPE_BUILDERS",
            {"rust-makefile-baseline": packages._makefile_envelope},
        )
        monkeypatch.setattr(packages, "_declared_input_kind", lambda _rule_dir: None)
        with pytest.raises(OperationalRuleError) as excinfo:
            packages.default_envelope_builder("rust-build-defaults", checkout)
        message = str(excinfo.value)
        assert "rust-makefile-baseline" in message, (
            f"the registered set should be listed, got {message!r}"
        )
        assert ENVELOPE_KIND in message, (
            f"the declarable input kinds should be listed, got {message!r}"
        )

    def test_an_unknown_declared_kind_is_refused_by_name(
        self, checkout: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A typo in a manifest must name the typo, not merely the package."""
        monkeypatch.setattr(packages, "PACKAGE_ENVELOPE_BUILDERS", {})
        monkeypatch.setattr(
            packages, "_declared_input_kind", lambda _rule_dir: "policy-input/typo"
        )
        with pytest.raises(OperationalRuleError) as excinfo:
            packages.default_envelope_builder("rust-build-defaults", checkout)
        assert "policy-input/typo" in str(excinfo.value), (
            f"the unknown kind should be quoted, got {excinfo.value!s}"
        )

    def test_run_rule_surfaces_the_refusal(
        self, checkout: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Driven through the public boundary, because that is what an operator runs.

        The command reports an operational failure and exits 2 rather than
        producing a verdict, which is the whole point of refusing.
        """
        monkeypatch.setattr(packages, "PACKAGE_ENVELOPE_BUILDERS", {})
        monkeypatch.setattr(packages, "_declared_input_kind", lambda _rule_dir: None)
        with pytest.raises(OperationalRuleError) as excinfo:
            runner.run_rule("rust-build-defaults", checkout)
        assert excinfo.value.operation == "select-policy-envelope", (
            "the refusal must reach the caller rather than becoming a finding"
        )


class TestDeclaredInputKind:
    """A package may name its input in its own manifest instead."""

    def test_the_shipped_manifests_declare_their_kind(self) -> None:
        """Both routes must agree, or the manifests document a fiction."""
        for rule_id, kind in (
            ("rust-makefile-baseline", ENVELOPE_KIND),
            ("rust-build-defaults", BUILD_DEFAULTS_ENVELOPE_KIND),
        ):
            rule_dir = packages.rule_package_dir(rule_id)
            declared = packages._declared_input_kind(rule_dir)
            assert declared == kind, (
                f"{rule_id} declares {declared!r}, but resolves to {kind!r}"
            )

    def test_a_declared_kind_resolves_without_a_registration(
        self, checkout: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The route that lets a new package ship without a Python edit."""
        monkeypatch.setattr(packages, "PACKAGE_ENVELOPE_BUILDERS", {})
        monkeypatch.setattr(
            packages,
            "_declared_input_kind",
            lambda _rule_dir: BUILD_DEFAULTS_ENVELOPE_KIND,
        )
        envelope = packages.default_envelope_builder("rust-build-defaults", checkout)
        assert envelope["kind"] == BUILD_DEFAULTS_ENVELOPE_KIND, (
            "a declared kind must select the builder that produces it"
        )

    def test_a_manifest_without_a_sensor_declares_nothing(
        self, tmp_path: pathlib.Path
    ) -> None:
        """An absent declaration is not a malformed one, and must not raise."""
        (tmp_path / "rule.yaml").write_text(
            "schema_version: 1\nid: x\n", encoding="utf-8"
        )
        assert packages._declared_input_kind(tmp_path) is None, (
            "a manifest with no sensor block declares no input kind"
        )

    def test_a_non_string_declaration_declares_nothing(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A list or mapping where a kind belongs is refused, not coerced."""
        (tmp_path / "rule.yaml").write_text(
            "sensor:\n  input:\n    - policy-input/rust-build-defaults\n",
            encoding="utf-8",
        )
        assert packages._declared_input_kind(tmp_path) is None, (
            "only a string names an input kind"
        )


def test_the_makefile_adapter_ignores_parameters(
    checkout: pathlib.Path,
) -> None:
    """The adapter exists to give one callable type, not to drop facts.

    `rust-makefile-baseline` reads its tunables through `data.parameters` in
    the policy, so the envelope needs none; passing some must not change what
    it builds.
    """
    parameters: cabc.Mapping[str, object] = {"gate_variable": "SOMETHING_ELSE"}
    with_parameters = packages._makefile_envelope(checkout, parameters)
    without = packages._makefile_envelope(checkout)
    assert with_parameters == without, (
        "the Makefile envelope does not vary with parameters"
    )
