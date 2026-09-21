"""Unit tests for `.cargo/config.toml` fact extraction.

The facts here decide what the `rust-build-defaults` policy is allowed to
conclude, so each test pins one reading the policy depends on. Spelling
variations matter more than usual: the estate writes the same linker flag
three different ways, and a reader that recognises only one of them would
fail the repositories that already comply.
"""

from __future__ import annotations

import typing as typ

import pytest

from concordat.rules.cargo_config import (
    inspect_cargo_config,
    normalise_flags,
    read_cargo_config,
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
        """Axinite's split spelling must read as netsuke's joined one."""
        assert normalise_flags(value) == expected

    def test_a_trailing_value_taking_flag_is_kept_verbatim(self) -> None:
        """A dangling `-C` has no value to join, and inventing one would lie."""
        assert normalise_flags(["-Zthreads=8", "-C"]) == ["-Zthreads=8", "-C"]

    def test_a_non_list_value_yields_no_flags(self) -> None:
        """Cargo reads neither an integer nor a table as rustflags."""
        assert normalise_flags(17) == []


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
        assert facts is not None
        names = [source["name"] for source in facts["sources"]]
        assert names == ["build", f"target.{LINUX_CFG}"]

    @pytest.mark.parametrize(
        ("key", "is_linux", "is_classified"),
        [
            pytest.param(LINUX_CFG, True, True, id="linux-cfg"),
            pytest.param("x86_64-unknown-linux-gnu", True, True, id="linux-triple"),
            pytest.param("cfg(windows)", False, True, id="windows-cfg"),
            pytest.param("aarch64-apple-darwin", False, True, id="darwin-triple"),
            pytest.param('cfg(target_env = "gnu")', False, False, id="unclassified"),
        ],
    )
    def test_target_keys_are_classified_for_linux(
        self,
        tmp_path: pathlib.Path,
        key: str,
        is_linux: bool,  # noqa: FBT001 - parametrised expectation, not a flag
        is_classified: bool,  # noqa: FBT001 - parametrised expectation
    ) -> None:
        """A key the reader cannot place is unclassified rather than guessed."""
        write_config(tmp_path, f"[target.'{key}']\nrustflags = []\n")
        facts = inspect_cargo_config(tmp_path)
        assert facts is not None
        source = facts["sources"][0]
        assert source["linux"] is is_linux
        assert source["classified"] is is_classified

    def test_a_nested_table_beneath_a_target_is_not_a_source(
        self, tmp_path: pathlib.Path
    ) -> None:
        """`[target.<triple>.<links>]` holds build-script overrides, not flags."""
        write_config(
            tmp_path,
            "[target.'cfg(unix)'.foo]\nrustflags = [\"-Zthreads=8\"]\n",
        )
        facts = inspect_cargo_config(tmp_path)
        assert facts is not None
        assert facts["sources"] == []


class TestCodegenBackends:
    """Each route to a backend is found, and nothing else is reported as one."""

    @pytest.mark.parametrize(
        ("body", "expected"),
        [
            pytest.param(
                '[profile.dev]\ncodegen-backend = "cranelift"\n',
                [("profile.dev", "dev", "cranelift")],
                id="profile-key",
            ),
            pytest.param(
                '[profile.release.package."*"]\ncodegen-backend = "cranelift"\n',
                [("profile.release.package.*", "release", "cranelift")],
                id="package-override",
            ),
            pytest.param(
                '[build]\nrustflags = ["-Zcodegen-backend=cranelift"]\n',
                [("build", None, "cranelift")],
                id="rustflags",
            ),
        ],
    )
    def test_every_documented_route_is_reported(
        self,
        tmp_path: pathlib.Path,
        body: str,
        expected: list[tuple[str, str | None, str]],
    ) -> None:
        """Closing one route while another stays open is not a refusal."""
        write_config(tmp_path, body)
        facts = inspect_cargo_config(tmp_path)
        assert facts is not None
        found = [
            (backend["source"], backend["profile"], backend["backend"])
            for backend in facts["backends"]
        ]
        assert found == expected

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
        assert facts is not None
        assert facts["backends"] == []

    def test_the_unstable_table_is_read(self, tmp_path: pathlib.Path) -> None:
        """Weaver's spelling pairs the profile key with the unstable gate."""
        write_config(
            tmp_path,
            "[unstable]\ncodegen-backend = true\n"
            '[profile.dev]\ncodegen-backend = "cranelift"\n',
        )
        facts = inspect_cargo_config(tmp_path)
        assert facts is not None
        assert facts["unstable_codegen_backend"] is True

    def test_the_unstable_gate_is_absent_by_default(
        self, tmp_path: pathlib.Path
    ) -> None:
        """An absent gate is `None`, distinct from one explicitly set false."""
        write_config(tmp_path, '[build]\nrustflags = ["-Zthreads=8"]\n')
        facts = inspect_cargo_config(tmp_path)
        assert facts is not None
        assert facts["unstable_codegen_backend"] is None


class TestReadFailures:
    """A file that cannot be read is reported, never treated as absent."""

    def test_an_absent_configuration_has_no_facts(self, tmp_path: pathlib.Path) -> None:
        """No file means no facts, which the policy reads as noncompliant."""
        assert inspect_cargo_config(tmp_path) is None

    def test_malformed_toml_is_reported_rather_than_raised(
        self, tmp_path: pathlib.Path
    ) -> None:
        """An unparsable file must fail closed, not vanish into an absence."""
        write_config(tmp_path, "[build\nrustflags = []\n")
        facts = inspect_cargo_config(tmp_path)
        assert facts is not None
        assert facts["parse_error"] is not None
        assert facts["sources"] == []

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
        assert facts is not None
        assert facts["sources"][0]["flags"] == []

    def test_a_directory_in_place_of_the_file_is_reported(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A path that is not a regular file is an absence, not a crash."""
        (tmp_path / ".cargo" / "config.toml").mkdir(parents=True)
        assert inspect_cargo_config(tmp_path) is None

    def test_the_legacy_extensionless_name_is_read(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Cargo still reads `.cargo/config`, so the reader must too."""
        legacy = tmp_path / ".cargo" / "config"
        legacy.parent.mkdir(parents=True)
        legacy.write_text('[build]\nrustflags = ["-Zthreads=8"]\n', encoding="utf-8")
        facts = inspect_cargo_config(tmp_path)
        assert facts is not None
        assert facts["path"] == ".cargo/config"


def test_read_cargo_config_returns_the_parsed_document(
    tmp_path: pathlib.Path,
) -> None:
    """The document reader is separable from the fact extraction above."""
    write_config(tmp_path, '[build]\nrustflags = ["-Zthreads=8"]\n')
    document = read_cargo_config(tmp_path / ".cargo" / "config.toml")
    assert document == {"build": {"rustflags": ["-Zthreads=8"]}}
