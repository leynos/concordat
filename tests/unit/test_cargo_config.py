"""Unit tests for `.cargo/config.toml` fact extraction.

The facts here decide what the `rust-build-defaults` policy is allowed to
conclude, so each test pins one reading the policy depends on. Spelling
variations matter more than usual: the estate writes the same linker flag
three different ways, and a reader that recognizes only one of them would
fail the repositories that already comply.
"""

from __future__ import annotations

import typing as typ

import pytest

from concordat.rules.cargo_config import (
    SCOPE_PACKAGE_OVERRIDE,
    SCOPE_PROFILE,
    SCOPE_RUSTFLAGS,
    classify_target_key,
    inspect_cargo_config,
    normalise_flags,
    read_cargo_config,
    rustflags_cargo_would_refuse,
)

if typ.TYPE_CHECKING:
    import pathlib

LINUX_CFG: typ.Final = 'cfg(target_os = "linux")'


def write_config(root: pathlib.Path, body: str) -> pathlib.Path:
    """Write *body* to the checkout's `.cargo/config.toml` and return it."""
    config = root / ".cargo" / "config.toml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(body, encoding="utf-8")
    return config


class TestFlagNormalisation:
    """A flag means the same thing however Cargo lets it be spelled."""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            pytest.param(
                ["-Zthreads=8", "-Clink-arg=-fuse-ld=mold"],
                ["-Zthreads=8", "-Clink-arg=-fuse-ld=mold"],
                id="joined-array",
            ),
            pytest.param(
                ["-C", "link-arg=-fuse-ld=mold"],
                ["-Clink-arg=-fuse-ld=mold"],
                id="split-array",
            ),
            pytest.param(
                "-Zthreads=8 -C link-arg=-fuse-ld=mold",
                ["-Zthreads=8", "-Clink-arg=-fuse-ld=mold"],
                id="space-separated-string",
            ),
        ],
    )
    def test_equivalent_spellings_normalise_alike(
        self, value: object, expected: list[str]
    ) -> None:
        """The split spelling axinite uses must read as netsuke's joined one."""
        assert normalise_flags(value) == expected, (
            f"{value!r} should normalize to {expected!r}"
        )

    def test_a_trailing_value_taking_flag_is_kept_verbatim(self) -> None:
        """A dangling `-C` has no value to join, and inventing one would lie."""
        assert normalise_flags(["-Zthreads=8", "-C"]) == ["-Zthreads=8", "-C"], (
            "a value-taking flag with nothing after it is reported as written"
        )

    def test_a_non_list_value_yields_no_flags(self) -> None:
        """Cargo reads neither an integer nor a table as rustflags."""
        assert normalise_flags(17) == [], "a non-flag value carries no flags"


class TestRustflagsCargoWouldRefuse:
    """A configuration Cargo exits on is not one to read the standard from."""

    def test_a_string_and_an_array_of_strings_are_accepted(self) -> None:
        """Both spellings Cargo documents pass through unremarked."""
        document: dict[str, object] = {
            "build": {"rustflags": "-Zthreads=8"},
            "target": {LINUX_CFG: {"rustflags": ["-Zthreads=8"]}},
        }
        assert rustflags_cargo_would_refuse(document) is None, (
            "the two documented spellings must not be reported as refusals"
        )

    @pytest.mark.parametrize(
        ("value", "fragment"),
        [
            pytest.param(["-Zthreads=8", 42], "contains a int", id="member"),
            pytest.param(17, "is int", id="whole-value"),
        ],
    )
    def test_a_value_cargo_rejects_is_reported(
        self, value: object, fragment: str
    ) -> None:
        """Cargo exits on either; skipping the bad part would read a broken file."""
        document: dict[str, object] = {"build": {"rustflags": value}}
        refusal = rustflags_cargo_would_refuse(document)
        assert refusal is not None, f"cargo refuses rustflags = {value!r}"
        assert fragment in refusal, f"{refusal!r} should name the offending type"

    def test_the_offending_target_table_is_named(self) -> None:
        """The diagnostic points at the source, not merely at the file."""
        document: dict[str, object] = {
            "build": {"rustflags": ["-Zthreads=8"]},
            "target": {LINUX_CFG: {"rustflags": ["-Zthreads=8", None]}},
        }
        refusal = rustflags_cargo_would_refuse(document)
        assert refusal is not None, "a target table is read the same way"
        assert LINUX_CFG in refusal, f"{refusal!r} should name the target table"


class TestRustflagsSources:
    """Every table Cargo would consult becomes one source."""

    def test_build_and_target_tables_are_both_sources(
        self, tmp_path: pathlib.Path
    ) -> None:
        """`[build]` and each `[target.*]` table replace one another."""
        write_config(
            tmp_path,
            '[build]\nrustflags = ["-Zthreads=8"]\n\n'
            f"[target.'{LINUX_CFG}']\n"
            'rustflags = ["-Zthreads=8", "-Clink-arg=-fuse-ld=mold"]\n',
        )
        facts = inspect_cargo_config(tmp_path)
        assert facts is not None, "a written configuration must produce facts"
        names = [source["name"] for source in facts["sources"]]
        assert names == ["build", f"target.{LINUX_CFG}"], (
            f"both rustflags sources should be reported, got {names!r}"
        )

    def test_a_nested_table_beneath_a_target_is_not_a_source(
        self, tmp_path: pathlib.Path
    ) -> None:
        """`[target.<triple>.<links>]` holds build-script overrides, not flags."""
        write_config(
            tmp_path,
            "[target.'cfg(unix)'.foo]\nrustflags = [\"-Zthreads=8\"]\n",
        )
        facts = inspect_cargo_config(tmp_path)
        assert facts is not None, "the file exists, so facts are produced"
        assert facts["sources"] == [], (
            "a build-script override is not a target flag source"
        )


class TestTargetClassification:
    """Where a target table applies, and when that cannot be decided."""

    @pytest.mark.parametrize(
        ("key", "expected"),
        [
            pytest.param(LINUX_CFG, (True, True, True), id="linux-cfg"),
            pytest.param(
                "x86_64-unknown-linux-gnu", (True, True, True), id="linux-triple"
            ),
            pytest.param("cfg(windows)", (False, False, True), id="windows-cfg"),
            pytest.param(
                "aarch64-apple-darwin", (False, False, True), id="darwin-triple"
            ),
            # A source Linux builds take, and so do macOS builds: the linker
            # ships for Linux alone, so this is not a place to name it.
            pytest.param("cfg(unix)", (True, False, True), id="unix-cfg"),
            # A negated expression contains the Linux predicate and applies
            # everywhere except Linux; a substring test reads it backwards.
            pytest.param(
                'cfg(not(target_os = "linux"))', (False, False, False), id="negated"
            ),
            # A disjunction widens the verdict to every platform it names.
            pytest.param(
                'cfg(any(target_os = "linux", target_os = "macos"))',
                (False, False, False),
                id="disjunction",
            ),
            pytest.param(
                'cfg(target_env = "gnu")', (False, False, False), id="unclassified"
            ),
            # Cargo accepts a custom JSON target as a target key. It is a path,
            # not a triple, and the `-linux` in its name says nothing about
            # what it targets.
            pytest.param(
                "custom-linux.json", (False, False, False), id="custom-json-target"
            ),
            # A near miss on the triple shape: two components, not three or
            # four, so it is not a triple this reader will read as one.
            pytest.param("custom-linux", (False, False, False), id="near-miss"),
            # The dangerous custom target: a path to a JSON file named after
            # the triple it is derived from. It splits into four components
            # and one of them is exactly `linux`, so only the component shape
            # keeps it from being read as a Linux-only triple.
            pytest.param(
                "targets/x86_64-unknown-linux-gnu.json",
                (False, False, False),
                id="custom-json-path-named-after-a-triple",
            ),
            # `i686-linux-android` names two operating systems this reader
            # knows, and which is the OS and which the environment depends on
            # a reading of the three-component form that is not decidable from
            # the name alone.
            pytest.param(
                "i686-linux-android", (False, False, False), id="ambiguous-triple"
            ),
            # An architecture with no operating system component at all. It is
            # decidably not Linux without naming an OS.
            pytest.param(
                "wasm32-unknown-unknown", (False, False, True), id="wasm-triple"
            ),
            pytest.param(
                "x86_64-unknown-linux-musl", (True, True, True), id="musl-triple"
            ),
            pytest.param(
                "totally-made-up-key", (False, False, False), id="unrecognized"
            ),
        ],
    )
    def test_keys_are_placed_or_left_unplaced(
        self, key: str, expected: tuple[bool, bool, bool]
    ) -> None:
        """A key the reader cannot place is unclassified rather than guessed."""
        classification = classify_target_key(key)
        assert tuple(classification) == expected, (
            f"{key!r} should classify as {expected!r}, got {tuple(classification)!r}"
        )

    def test_a_custom_target_is_not_read_as_a_triple_in_a_configuration(
        self, tmp_path: pathlib.Path
    ) -> None:
        """The end-to-end path, because the policy reads the source not the key.

        A custom JSON target whose filename contains `linux` would otherwise
        be reported as a Linux-only source, and BD-002 would then demand the
        Linux-only linker flag in a table that may target anything at all.
        """
        write_config(
            tmp_path,
            "[target.'custom-linux.json']\nrustflags = [\"-Zthreads=8\"]\n",
        )
        facts = inspect_cargo_config(tmp_path)
        assert facts is not None, "the file exists, so facts are produced"
        source = facts["sources"][0]
        assert source["classified"] is False, (
            "a custom target is a path, not a triple, and cannot be placed"
        )
        assert source["linux"] is False, "an unplaced source claims nothing"

    def test_the_classification_reaches_the_source(
        self, tmp_path: pathlib.Path
    ) -> None:
        """The policy reads these fields from the source, not from the key."""
        write_config(tmp_path, "[target.'cfg(unix)']\nrustflags = []\n")
        facts = inspect_cargo_config(tmp_path)
        assert facts is not None, "the file exists, so facts are produced"
        source = facts["sources"][0]
        assert source["linux"] is True, "cfg(unix) is a source Linux builds take"
        assert source["linux_only"] is False, "cfg(unix) reaches macOS as well"


class TestCodegenBackends:
    """Each route to a backend is found, and nothing else is reported as one."""

    @pytest.mark.parametrize(
        ("body", "expected"),
        [
            pytest.param(
                '[profile.dev]\ncodegen-backend = "cranelift"\n',
                [("profile.dev", SCOPE_PROFILE, "dev", "cranelift")],
                id="profile-key",
            ),
            pytest.param(
                '[profile.release.package."*"]\ncodegen-backend = "cranelift"\n',
                [
                    (
                        "profile.release.package.*",
                        SCOPE_PACKAGE_OVERRIDE,
                        "release",
                        "cranelift",
                    )
                ],
                id="package-override",
            ),
            pytest.param(
                '[build]\nrustflags = ["-Zcodegen-backend=cranelift"]\n',
                [("build", SCOPE_RUSTFLAGS, None, "cranelift")],
                id="rustflags",
            ),
        ],
    )
    def test_every_documented_route_is_reported_with_its_scope(
        self,
        tmp_path: pathlib.Path,
        body: str,
        expected: list[tuple[str, str, str | None, str]],
    ) -> None:
        """Closing one route while another stays open is not a refusal.

        The scope is recorded because the routes are not interchangeable: a
        package override selects a backend for one package, so it is a
        selection to validate but never the profile's default.
        """
        write_config(tmp_path, body)
        facts = inspect_cargo_config(tmp_path)
        assert facts is not None, "the file exists, so facts are produced"
        found = [
            (
                backend["source"],
                backend["scope"],
                backend["profile"],
                backend["backend"],
            )
            for backend in facts["backends"]
        ]
        assert found == expected, f"expected {expected!r}, got {found!r}"

    def test_an_unrelated_key_of_the_same_name_is_not_a_backend(
        self, tmp_path: pathlib.Path
    ) -> None:
        """The word is not reserved; only the documented paths select a backend."""
        write_config(
            tmp_path,
            '[features]\ncodegen-backend = ["dep:something"]\n'
            "[profile.dev.package.thing]\nopt-level = 3\n",
        )
        facts = inspect_cargo_config(tmp_path)
        assert facts is not None, "the file exists, so facts are produced"
        assert facts["backends"] == [], (
            "only the documented key paths are backend selections"
        )

    def test_the_unstable_table_is_read(self, tmp_path: pathlib.Path) -> None:
        """The spelling weaver uses pairs the profile key with the unstable gate."""
        write_config(
            tmp_path,
            "[unstable]\ncodegen-backend = true\n"
            '[profile.dev]\ncodegen-backend = "cranelift"\n',
        )
        facts = inspect_cargo_config(tmp_path)
        assert facts is not None, "the file exists, so facts are produced"
        assert facts["unstable_codegen_backend"] is True, (
            "the unstable gate must be reported when it is set"
        )

    def test_the_unstable_gate_is_absent_by_default(
        self, tmp_path: pathlib.Path
    ) -> None:
        """An absent gate is `None`, distinct from one explicitly set false."""
        write_config(tmp_path, '[build]\nrustflags = ["-Zthreads=8"]\n')
        facts = inspect_cargo_config(tmp_path)
        assert facts is not None, "the file exists, so facts are produced"
        assert facts["unstable_codegen_backend"] is None, (
            "an absent gate is distinguishable from one set to false"
        )


class TestReadFailures:
    """A file that cannot be read is reported, never treated as absent."""

    def test_an_absent_configuration_has_no_facts(self, tmp_path: pathlib.Path) -> None:
        """No file means no facts, which the policy reads as noncompliant."""
        assert inspect_cargo_config(tmp_path) is None, (
            "a checkout with no configuration produces no facts"
        )

    def test_malformed_toml_is_reported_rather_than_raised(
        self, tmp_path: pathlib.Path
    ) -> None:
        """An unparsable file must fail closed, not vanish into an absence."""
        write_config(tmp_path, "[build\nrustflags = []\n")
        facts = inspect_cargo_config(tmp_path)
        assert facts is not None, "an unparsable file still produces facts"
        assert facts["parse_error"] is not None, "the reason must be carried"
        assert facts["sources"] == [], "nothing was read, so no source is reported"

    def test_a_value_cargo_refuses_is_carried_as_a_parse_error(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Cargo exits on this file, so the audit must decide nothing from it."""
        write_config(tmp_path, '[build]\nrustflags = ["-Zthreads=8", 42]\n')
        facts = inspect_cargo_config(tmp_path)
        assert facts is not None, "the file exists, so facts are produced"
        assert facts["parse_error"] is not None, (
            "a configuration cargo refuses must fail closed"
        )
        assert facts["sources"] == [], (
            "reading the remaining flags would pass a repository that cannot build"
        )

    def test_a_comment_naming_the_flags_contributes_nothing(
        self, tmp_path: pathlib.Path
    ) -> None:
        """The parser is the point: a commented flag is not a configured one."""
        write_config(
            tmp_path,
            "# -Zthreads=8 and -Clink-arg=-fuse-ld=mold are the standard\n"
            "[build]\nrustflags = []\n",
        )
        facts = inspect_cargo_config(tmp_path)
        assert facts is not None, "the file exists, so facts are produced"
        assert facts["sources"][0]["flags"] == [], (
            "a flag named in a comment is not configured"
        )

    def test_a_directory_in_place_of_the_file_is_reported(
        self, tmp_path: pathlib.Path
    ) -> None:
        """An occupied path is a misconfiguration, not a missing configuration.

        The filesystem answered, but it did not answer that nothing is there.
        Reading it as an absence would report the repository as one that never
        wrote a configuration rather than one whose configuration cannot be
        read.
        """
        (tmp_path / ".cargo" / "config.toml").mkdir(parents=True)
        facts = inspect_cargo_config(tmp_path)
        assert facts is not None, "an occupied path is not an absence"
        assert facts["parse_error"] is not None, (
            "the reason the path could not be read must reach the policy"
        )

    def test_a_filesystem_refusal_is_not_an_absence(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A permission failure must not read as a repository with no standard."""
        write_config(tmp_path, '[build]\nrustflags = ["-Zthreads=8"]\n')

        def refuse(_self: pathlib.Path, *_args: object, **_kwargs: object) -> object:
            message = "Permission denied"
            raise PermissionError(13, message)

        monkeypatch.setattr("pathlib.Path.stat", refuse)
        facts = inspect_cargo_config(tmp_path)
        assert facts is not None, "a refusal is not an absence"
        assert facts["parse_error"] is not None, (
            "the refusal's reason must reach the policy"
        )

    def test_the_legacy_extensionless_name_is_read(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Cargo still reads `.cargo/config`, so the reader must too."""
        legacy = tmp_path / ".cargo" / "config"
        legacy.parent.mkdir(parents=True)
        legacy.write_text('[build]\nrustflags = ["-Zthreads=8"]\n', encoding="utf-8")
        facts = inspect_cargo_config(tmp_path)
        assert facts is not None, "the legacy name is a configuration cargo reads"
        assert facts["path"] == ".cargo/config", (
            f"the reported path should be the file read, got {facts['path']!r}"
        )


def test_read_cargo_config_returns_the_parsed_document(
    tmp_path: pathlib.Path,
) -> None:
    """The document reader is separable from the fact extraction above."""
    write_config(tmp_path, '[build]\nrustflags = ["-Zthreads=8"]\n')
    document = read_cargo_config(tmp_path / ".cargo" / "config.toml")
    assert document == {"build": {"rustflags": ["-Zthreads=8"]}}, (
        f"the parsed document should mirror the file, got {document!r}"
    )
