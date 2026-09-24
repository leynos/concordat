"""The envelope resolver refuses a package it cannot choose an input for.

A rule package reads facts of one shape and its policy expects that shape.
When the two disagree the policy does not degrade: it answers confidently
about a document it was never written for, and the operator sees a verdict
rather than a failure. The resolver therefore has no fallback, and these
tests pin both halves of that — the packages it does resolve, and the refusal
for everything else.
"""

from __future__ import annotations

import pathlib
import typing as typ

import pytest

from concordat import cli
from concordat.errors import OperationalRuleError
from concordat.rules import packages, runner
from concordat.rules.codescene_coverage_envelope import (
    ENVELOPE_KIND as COVERAGE_ENVELOPE_KIND,
)
from concordat.rules.dependabot_envelope import (
    ENVELOPE_KIND as DEPENDABOT_ENVELOPE_KIND,
)
from concordat.rules.envelope import (
    BUILD_DEFAULTS_ENVELOPE_KIND,
    ENVELOPE_KIND,
)
from concordat.rules.markdown_envelope import (
    ENVELOPE_KIND as MARKDOWN_ENVELOPE_KIND,
)

if typ.TYPE_CHECKING:
    import collections.abc as cabc

CARGO: typ.Final = '[package]\nname = "fixture"\nversion = "0.1.0"\n'


def _shipped_packages() -> set[str]:
    """Return the name of every rule package that ships a policy."""
    return {
        path.name
        for path in packages._rule_packages_dir().iterdir()
        if (path / "policy").is_dir()
    }


def _undeclarable_builders(
    by_id: cabc.Mapping[str, typ.Any],
    by_kind: cabc.Mapping[str, typ.Any],
) -> set[str]:
    """Return the names of builders reachable by identifier but not by kind.

    The invariant is a subset rather than an equality: a builder reachable by
    kind and not by identifier is the declared-only package the guide
    promises, and must not be a violation. Expressed as one function so the
    shipped mappings and the constructed cases are judged by the same rule
    rather than by two restatements of it.

    Returns
    -------
    set[str]
        The offending builder names, empty when the invariant holds.
    """
    return {builder.__name__ for builder in set(by_id.values()) - set(by_kind.values())}


def _is_resolvable(rule_id: str) -> bool:
    """Report whether *rule_id* reaches a builder by either documented route."""
    if rule_id in packages.PACKAGE_ENVELOPE_BUILDERS:
        return True
    declared = packages._declared_input_kind(packages.rule_package_dir(rule_id))
    return declared in packages.INPUT_KIND_ENVELOPE_BUILDERS


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
            pytest.param(
                "markdown-formatting-baseline",
                MARKDOWN_ENVELOPE_KIND,
                id="markdown",
            ),
            pytest.param(
                "main-owned-codescene-coverage",
                COVERAGE_ENVELOPE_KIND,
                id="codescene-coverage",
            ),
            pytest.param(
                "dependabot-update-shape",
                DEPENDABOT_ENVELOPE_KIND,
                id="dependabot-update-shape",
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

    def test_every_shipped_package_is_resolvable(self) -> None:
        """Every package reaches an envelope by one route or the other.

        Without this, adding a package and wiring up neither route is caught
        only when someone runs it, and this suite would pass over the gap.
        The check spans both routes deliberately: requiring registration alone
        would refuse the manifest-only route the developers' guide promises.
        """
        shipped = _shipped_packages()
        assert shipped, "the discovery must find the shipped packages"
        unresolvable = {name for name in shipped if not _is_resolvable(name)}
        assert unresolvable == set(), (
            f"these packages reach no envelope builder: {sorted(unresolvable)}"
        )

    def test_a_package_is_resolvable_by_its_manifest_alone(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The promise that reusing an envelope kind needs no Python change.

        With the identifier mapping emptied, every shipped package must still
        resolve, because each declares its input kind. A guard written against
        registration alone would fail a package that took the documented
        route, which is the shape this test exists to forbid.
        """
        monkeypatch.setattr(packages, "PACKAGE_ENVELOPE_BUILDERS", {})
        unresolvable = {
            name for name in _shipped_packages() if not _is_resolvable(name)
        }
        assert unresolvable == set(), (
            f"these packages resolve only by registration: {sorted(unresolvable)}"
        )

    def test_every_registered_builder_is_also_declarable(self) -> None:
        """A registered builder must be reachable by the kind it emits.

        Otherwise a package's envelope is one no other package can declare,
        and the package's own manifest declaration names a kind the resolver
        does not know — an inconsistency that only shows up if its
        registration is ever removed.
        """
        undeclarable = _undeclarable_builders(
            packages.PACKAGE_ENVELOPE_BUILDERS,
            packages.INPUT_KIND_ENVELOPE_BUILDERS,
        )
        assert undeclarable == set(), (
            "these builders are reachable by identifier but not by any "
            f"declarable kind: {sorted(undeclarable)}"
        )

    def test_a_declared_only_builder_satisfies_the_invariant(self) -> None:
        """The asymmetry is the point, and the shipped mappings cannot show it.

        Both shipped mappings hold the same two builders today, so the check
        above passes under a subset rule and under an equality rule alike, and
        proves nothing about which is in force. This constructs the case that
        separates them: a package bringing its own builder, adding one entry
        to the kind mapping and declaring that kind, with no identifier entry
        at all. That is the route the guide promises, and an equality rule
        would refuse it.
        """

        def build_third_envelope(
            _checkout: pathlib.Path, _parameters: object = None
        ) -> dict[str, object]:
            return {"kind": "policy-input/third"}

        by_id = dict(packages.PACKAGE_ENVELOPE_BUILDERS)
        by_kind = dict(packages.INPUT_KIND_ENVELOPE_BUILDERS) | {
            "policy-input/third": build_third_envelope
        }

        assert _undeclarable_builders(by_id, by_kind) == set(), (
            "a builder declarable but not registered must satisfy the rule"
        )
        assert set(by_id.values()) != set(by_kind.values()), (
            "this case must actually be asymmetric, or it separates nothing "
            "and an equality rule would pass it too"
        )

    def test_a_registered_builder_missing_its_kind_fails_the_invariant(
        self,
    ) -> None:
        """The other direction, so the subset rule is not vacuous.

        A rule satisfied by every arrangement forbids nothing. Dropping the
        kind entry for a registered builder must be caught, and the failure
        must name the builder rather than only report a mismatch.
        """
        by_id = dict(packages.PACKAGE_ENVELOPE_BUILDERS)
        dropped = packages.PACKAGE_ENVELOPE_BUILDERS["rust-makefile-baseline"]
        by_kind = {
            kind: builder
            for kind, builder in packages.INPUT_KIND_ENVELOPE_BUILDERS.items()
            if builder is not dropped
        }

        undeclarable = _undeclarable_builders(by_id, by_kind)

        assert undeclarable == {dropped.__name__}, (
            f"the missing builder should be named, got {sorted(undeclarable)}"
        )

    def test_every_declared_kind_is_one_the_resolver_knows(self) -> None:
        """A declaration naming an unknown kind is a typo nobody would notice.

        A package that also registers resolves by identifier first, so a
        misspelled `sensor.input` never reaches the kind mapping and never
        fails — until the registration goes, and then the package stops
        resolving for a reason written down months earlier.
        """
        unknown = {
            name: declared
            for name in _shipped_packages()
            if (
                declared := packages._declared_input_kind(
                    packages.rule_package_dir(name)
                )
            )
            is not None
            and declared not in packages.INPUT_KIND_ENVELOPE_BUILDERS
        }
        assert unknown == {}, (
            f"these manifests declare a kind the resolver cannot map: {unknown}"
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
            ("markdown-formatting-baseline", MARKDOWN_ENVELOPE_KIND),
            ("main-owned-codescene-coverage", COVERAGE_ENVELOPE_KIND),
            ("dependabot-update-shape", DEPENDABOT_ENVELOPE_KIND),
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

    @pytest.mark.parametrize(
        "manifest",
        [
            # A key with nothing under it decodes to `None`, which is the
            # shape a half-finished manifest has while someone is mid-edit.
            pytest.param("schema_version: 1\nid: x\nsensor:\n", id="null"),
            pytest.param("schema_version: 1\nid: x\nsensor: conftest\n", id="scalar"),
            pytest.param(
                "schema_version: 1\nid: x\nsensor:\n  - type: conftest\n", id="list"
            ),
        ],
    )
    def test_a_sensor_that_is_not_a_mapping_declares_nothing(
        self, tmp_path: pathlib.Path, manifest: str
    ) -> None:
        """A malformed `sensor` is refused, never quietly given an envelope.

        The hazard is specific: falling back here hands one policy the
        document another was written for, and the policy then answers
        confidently about facts it never asked for. Reported by
        jm-concordat-168, whose own fixture contributed the null and list
        shapes; the null one is the most likely to be written by hand.
        """
        (tmp_path / "rule.yaml").write_text(manifest, encoding="utf-8")
        assert packages._declared_input_kind(tmp_path) is None, (
            f"a sensor of this shape declares no input kind: {manifest!r}"
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


def _package_tree(
    root: pathlib.Path,
    name: str,
    manifest: str,
) -> pathlib.Path:
    """Write a rule package under *root* with *manifest* as its `rule.yaml`.

    A real directory and a real manifest, so the manifest reader under test is
    the shipped one rather than a substitute for it.

    Returns
    -------
    pathlib.Path
        The written package directory.
    """
    package = root / name
    (package / "policy").mkdir(parents=True)
    (package / "rule.yaml").write_text(manifest, encoding="utf-8")
    return package


@pytest.fixture
def package_root(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> cabc.Iterator[pathlib.Path]:
    """Point the package resolver at a temporary tree for one test."""
    root = tmp_path / "lint-rules"
    root.mkdir()
    monkeypatch.setattr(packages, "_resolve_rule_packages_dir", lambda: root)
    packages._rule_packages_dir.cache_clear()
    yield root
    packages._rule_packages_dir.cache_clear()


class TestAgainstRealManifests:
    """The manifest routes, driven through files rather than substitutes."""

    def test_a_real_declaration_selects_the_envelope(
        self,
        checkout: pathlib.Path,
        package_root: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The documented route, with nothing about it stubbed out.

        Only the identifier mapping is emptied. The manifest reader, the
        `rule.yaml` and the kind mapping are the shipped ones, so this fails
        if a real declaration does not in fact reach a builder.
        """
        _package_tree(
            package_root,
            "declared-only",
            "schema_version: 1\nid: declared-only\nsensor:\n"
            f"  type: conftest\n  input: {BUILD_DEFAULTS_ENVELOPE_KIND}\n",
        )
        monkeypatch.setattr(packages, "PACKAGE_ENVELOPE_BUILDERS", {})

        envelope = packages.default_envelope_builder("declared-only", checkout)

        assert envelope["kind"] == BUILD_DEFAULTS_ENVELOPE_KIND, (
            f"a declared kind must select its builder, got {envelope['kind']!r}"
        )

    def test_a_real_package_declaring_nothing_is_refused(
        self,
        checkout: pathlib.Path,
        package_root: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The refusal, likewise driven through a manifest that declares none."""
        _package_tree(
            package_root,
            "declares-nothing",
            "schema_version: 1\nid: declares-nothing\nsensor:\n  type: conftest\n",
        )
        monkeypatch.setattr(packages, "PACKAGE_ENVELOPE_BUILDERS", {})

        with pytest.raises(OperationalRuleError) as excinfo:
            packages.default_envelope_builder("declares-nothing", checkout)

        assert "declares-nothing" in str(excinfo.value), (
            f"the refusal must name the package, got {excinfo.value!s}"
        )


class TestTheCommandRefuses:
    """The operator's view: an exit status and a diagnostic, not a verdict.

    `run_rule` raising is only half the guarantee. What an operator runs is
    the command, and a refusal that reached them as a clean exit and an empty
    table would be indistinguishable from a compliant audit.
    """

    def test_the_command_exits_two_with_the_reason_and_no_verdict(
        self,
        checkout: pathlib.Path,
        package_root: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Exit 2, the reason on standard error, and nothing on standard out."""
        _package_tree(
            package_root,
            "declares-nothing",
            "schema_version: 1\nid: declares-nothing\nsensor:\n  type: conftest\n",
        )
        monkeypatch.setattr(packages, "PACKAGE_ENVELOPE_BUILDERS", {})

        exit_code = cli.main([
            "artefact",
            "rule",
            "run",
            "declares-nothing",
            "--repo",
            str(checkout),
        ])

        captured = capsys.readouterr()
        assert exit_code == 2, (
            f"an operational failure exits 2, not {exit_code}; "
            "1 would read as a finding and 0 as a clean audit"
        )
        assert "declares-nothing" in captured.err, (
            f"the reason belongs on standard error, got {captured.err!r}"
        )
        for verdict in ("compliant", "noncompliant", "indeterminate"):
            assert verdict not in captured.out, (
                f"a refused audit must print no verdict, got {captured.out!r}"
            )


class TestManifestReading:
    """`rule_manifest` is a boundary, so its failures are part of the contract."""

    def test_a_package_without_a_manifest_reads_as_empty(
        self, tmp_path: pathlib.Path
    ) -> None:
        """An absent manifest is not a malformed one."""
        assert packages.rule_manifest(tmp_path) == {}, (
            "a package shipping no rule.yaml declares nothing"
        )

    def test_a_manifest_that_is_a_directory_is_refused(
        self, tmp_path: pathlib.Path
    ) -> None:
        """An occupied path is not a package that declared nothing.

        Returning `{}` here hands the policy its own baked-in defaults in
        place of the ones the package declares, and loses the `sensor.input`
        declaration that decides which envelope it is audited over. That is
        this module's own subject one level down.
        """
        (tmp_path / "rule.yaml").mkdir()
        with pytest.raises(OperationalRuleError) as excinfo:
            packages.rule_manifest(tmp_path)
        assert "directory" in str(excinfo.value), (
            f"the diagnostic should name the occupant, got {excinfo.value!s}"
        )

    def test_a_dangling_manifest_link_is_refused(self, tmp_path: pathlib.Path) -> None:
        """A link to a manifest that is not there is not an absent manifest."""
        (tmp_path / "rule.yaml").symlink_to(tmp_path / "elsewhere.yaml")
        with pytest.raises(OperationalRuleError) as excinfo:
            packages.rule_manifest(tmp_path)
        assert "resolve" in str(excinfo.value), (
            f"the diagnostic should give the reason, got {excinfo.value!s}"
        )

    def test_a_manifest_the_filesystem_will_not_describe_is_refused(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A refusal to stat the manifest must not read as a package with none."""
        (tmp_path / "rule.yaml").write_text("id: x\n", encoding="utf-8")

        def refuse(_self: pathlib.Path, *_args: object, **_kwargs: object) -> object:
            message = "Permission denied"
            raise PermissionError(13, message)

        monkeypatch.setattr("pathlib.Path.stat", refuse)
        with pytest.raises(OperationalRuleError):
            packages.rule_manifest(tmp_path)

    def test_malformed_yaml_is_an_operational_failure(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A manifest that cannot be parsed must not read as declaring nothing."""
        (tmp_path / "rule.yaml").write_text("id: [unclosed\n", encoding="utf-8")
        with pytest.raises(OperationalRuleError) as excinfo:
            packages.rule_manifest(tmp_path)
        assert excinfo.value.operation == "load-rule-manifest", (
            "the failure needs a stable operation identifier for automation"
        )

    def test_a_manifest_that_is_not_a_mapping_is_refused(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Valid YAML of the wrong shape is still a manifest nobody can read."""
        (tmp_path / "rule.yaml").write_text("- one\n- two\n", encoding="utf-8")
        with pytest.raises(OperationalRuleError) as excinfo:
            packages.rule_manifest(tmp_path)
        assert "not a mapping" in str(excinfo.value), (
            f"the diagnostic should say what was wrong, got {excinfo.value!s}"
        )

    def test_an_unreadable_manifest_is_an_operational_failure(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A refusal to read is reported, never treated as an absent manifest."""
        (tmp_path / "rule.yaml").write_text("id: x\n", encoding="utf-8")

        def refuse(*_args: object, **_kwargs: object) -> str:
            message = "Permission denied"
            raise PermissionError(13, message)

        monkeypatch.setattr(pathlib.Path, "read_text", refuse)
        with pytest.raises(OperationalRuleError):
            packages.rule_manifest(tmp_path)
