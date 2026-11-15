"""Unit tests for the `policy-input/rust-test-environment` envelope builder.

The Rego suite proves what the policy makes of each envelope; these tests prove
what the builder records: the workspace members and the Clippy configuration
Clippy would load for each, the wrapper crates a member depends on, the
attributes that suppress the lint, and the files it refuses to judge.
"""

from __future__ import annotations

import typing as typ

import pytest

from concordat.errors import OperationalRuleError
from concordat.rules.test_environment_envelope import (
    DEFAULT_REQUIRED_PATHS,
    build_test_environment_envelope,
)

if typ.TYPE_CHECKING:
    import pathlib

    from concordat.rules.test_environment_envelope import TestEnvironmentEnvelope

PACKAGE = '[package]\nname = "demo"\nversion = "0.1.0"\n'
EXPECT = '#[expect(clippy::disallowed_methods, reason = "adapter")]\n'


def _lay_out(checkout: pathlib.Path, files: dict[str, str]) -> None:
    """Write *files*, creating directories."""
    for relative, text in files.items():
        path = checkout / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def _build(
    tmp_path: pathlib.Path,
    files: dict[str, str],
    parameters: dict[str, object] | None = None,
) -> TestEnvironmentEnvelope:
    """Lay out a checkout and return its envelope."""
    _lay_out(tmp_path, files)
    return build_test_environment_envelope(tmp_path, parameters)


def _kinds(envelope: TestEnvironmentEnvelope) -> list[tuple[str, int, str]]:
    """Return `(path, line, kind)` for each recorded suppression."""
    return [(s["path"], s["line"], s["kind"]) for s in envelope["suppressions"]]


def _macro(attribute: str) -> str:
    """Return a `macro_rules!` whose body emits a function with *attribute*."""
    body = f"{attribute} fn g() {{}}"
    return f"macro_rules! m {{ () => {{ {body} }}; }}\n"


def _workspace(*members: str, extra: str = "") -> str:
    """Return a virtual workspace manifest."""
    listed = ", ".join(f'"{m}"' for m in members)
    return f"[workspace]\nmembers = [{listed}]\n{extra}"


class TestApplicability:
    """A repository without a root manifest is not audited."""

    def test_no_manifest(self, tmp_path: pathlib.Path) -> None:
        """No `Cargo.toml` means not applicable, with nothing else read."""
        envelope = _build(tmp_path, {"src/lib.rs": "#![allow(clippy::all)]\n"})
        assert envelope["applicability"] == {"cargo_manifest": False}
        assert envelope["members"] == []
        assert envelope["suppressions"] == []

    def test_required_paths_default_and_override(self, tmp_path: pathlib.Path) -> None:
        """The required paths are the parameter, so one entry can be dropped."""
        files = {"Cargo.toml": PACKAGE}
        assert _build(tmp_path, files)["required_paths"] == list(DEFAULT_REQUIRED_PATHS)
        override: dict[str, object] = {"required_paths": ["std::env::set_var"]}
        assert _build(tmp_path, files, override)["required_paths"] == [
            "std::env::set_var"
        ]


class TestMembers:
    """Workspace members follow Cargo's `members` and `exclude` globs."""

    def test_root_package_is_a_member(self, tmp_path: pathlib.Path) -> None:
        """A single package has the root as its only member."""
        envelope = _build(tmp_path, {"Cargo.toml": PACKAGE})
        assert [m["path"] for m in envelope["members"]] == ["."]

    def test_globs_and_excludes(self, tmp_path: pathlib.Path) -> None:
        """Globbed members are found; excluded ones and non-crates are not."""
        envelope = _build(
            tmp_path,
            {
                "Cargo.toml": _workspace(
                    "crates/*", extra='exclude = ["crates/old"]\n'
                ),
                "crates/a/Cargo.toml": PACKAGE,
                "crates/old/Cargo.toml": PACKAGE,
                "crates/notes/readme.md": "not a crate\n",
            },
        )
        assert [m["path"] for m in envelope["members"]] == ["crates/a"]

    def test_root_package_and_members(self, tmp_path: pathlib.Path) -> None:
        """A root package with a `[workspace]` is a member beside the others."""
        envelope = _build(
            tmp_path,
            {
                "Cargo.toml": PACKAGE + '[workspace]\nmembers = ["a"]\n',
                "a/Cargo.toml": PACKAGE,
            },
        )
        assert [m["path"] for m in envelope["members"]] == [".", "a"]

    def test_path_dependencies_are_members_of_a_workspace(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Cargo enrols a crate a member depends on by path, listed or not."""
        envelope = _build(
            tmp_path,
            {
                "Cargo.toml": PACKAGE
                + '[workspace]\n[dependencies]\ninner = { path = "inner" }\n',
                "inner/Cargo.toml": PACKAGE
                + '[dependencies]\ndeep = { path = "../crates/deep" }\n',
                "crates/deep/Cargo.toml": PACKAGE,
            },
        )
        assert [m["path"] for m in envelope["members"]] == [".", "crates/deep", "inner"]

    def test_path_dependencies_need_a_workspace_table(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Without a `[workspace]`, a path dependency is its own package."""
        envelope = _build(
            tmp_path,
            {
                "Cargo.toml": PACKAGE + '[dependencies]\ninner = { path = "inner" }\n',
                "inner/Cargo.toml": PACKAGE,
            },
        )
        assert [m["path"] for m in envelope["members"]] == ["."]

    def test_excluded_and_outside_path_dependencies_are_not_members(
        self, tmp_path: pathlib.Path
    ) -> None:
        """An excluded crate, or one outside the root, is not enrolled."""
        checkout = tmp_path / "checkout"
        _lay_out(tmp_path / "sibling", {"Cargo.toml": PACKAGE})
        envelope = _build(
            checkout,
            {
                "Cargo.toml": PACKAGE
                + '[workspace]\nexclude = ["old"]\n[dependencies]\n'
                + 'old = { path = "old" }\nsibling = { path = "../sibling" }\n',
                "old/Cargo.toml": PACKAGE,
            },
        )
        assert [m["path"] for m in envelope["members"]] == ["."]

    def test_inherited_path_dependencies_resolve_from_the_root(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A `workspace = true` path is relative to the workspace root."""
        envelope = _build(
            tmp_path,
            {
                "Cargo.toml": PACKAGE
                + '[workspace.dependencies]\nshared = { path = "libs/shared" }\n'
                + "[dependencies]\nshared = { workspace = true }\n",
                "libs/shared/Cargo.toml": PACKAGE,
            },
        )
        assert [m["path"] for m in envelope["members"]] == [".", "libs/shared"]

    def test_glob_cannot_leave_the_checkout(self, tmp_path: pathlib.Path) -> None:
        """A member pattern that climbs out of the checkout is refused."""
        outside = tmp_path / "outside"
        _lay_out(outside, {"Cargo.toml": PACKAGE})
        checkout = tmp_path / "checkout"
        checkout.mkdir()
        with pytest.raises(OperationalRuleError, match="outside the checkout"):
            _build(checkout, {"Cargo.toml": _workspace("../outside")})


class TestClippyConfiguration:
    """Each member records the configuration Clippy itself would load."""

    def test_member_inherits_the_root_file(self, tmp_path: pathlib.Path) -> None:
        """With no file of its own, a member takes the nearest one above."""
        envelope = _build(
            tmp_path,
            {
                "Cargo.toml": _workspace("crates/a"),
                "clippy.toml": 'disallowed-methods = ["std::env::set_var"]\n',
                "crates/a/Cargo.toml": PACKAGE,
            },
        )
        [member] = envelope["members"]
        assert member["clippy"] == {
            "path": "clippy.toml",
            "disallowed": ["std::env::set_var"],
            "readable": True,
        }

    def test_member_file_shadows_the_root_file(self, tmp_path: pathlib.Path) -> None:
        """A member's own file replaces the root's; it is not merged."""
        envelope = _build(
            tmp_path,
            {
                "Cargo.toml": _workspace("crates/a"),
                "clippy.toml": 'disallowed-methods = ["std::env::set_var"]\n',
                "crates/a/Cargo.toml": PACKAGE,
                "crates/a/clippy.toml": "too-many-lines-threshold = 70\n",
            },
        )
        [member] = envelope["members"]
        assert member["clippy"]["path"] == "crates/a/clippy.toml"
        assert member["clippy"]["disallowed"] == []

    def test_dotted_name_wins_in_one_directory(self, tmp_path: pathlib.Path) -> None:
        """Clippy tries `.clippy.toml` before `clippy.toml` in a directory."""
        envelope = _build(
            tmp_path,
            {
                "Cargo.toml": PACKAGE,
                ".clippy.toml": 'disallowed-methods = ["a::b"]\n',
                "clippy.toml": 'disallowed-methods = ["c::d"]\n',
            },
        )
        assert envelope["members"][0]["clippy"]["path"] == ".clippy.toml"

    def test_string_and_table_entries(self, tmp_path: pathlib.Path) -> None:
        """Both entry forms count, with their reasons ignored."""
        envelope = _build(
            tmp_path,
            {
                "Cargo.toml": PACKAGE,
                "clippy.toml": (
                    'disallowed-methods = ["a::b", { path = "c::d", reason = "x" },'
                    ' { reason = "no path" }]\n'
                ),
            },
        )
        assert envelope["members"][0]["clippy"]["disallowed"] == ["a::b", "c::d"]

    def test_no_file(self, tmp_path: pathlib.Path) -> None:
        """A member with no configuration says so."""
        envelope = _build(tmp_path, {"Cargo.toml": PACKAGE})
        assert envelope["members"][0]["clippy"] == {
            "path": None,
            "disallowed": [],
            "readable": True,
        }

    def test_invalid_file_is_unreadable_not_empty(self, tmp_path: pathlib.Path) -> None:
        """An invalid file is reported, and the member is marked unreadable."""
        envelope = _build(tmp_path, {"Cargo.toml": PACKAGE, "clippy.toml": "a = [\n"})
        assert envelope["members"][0]["clippy"]["readable"] is False
        assert [u.split(":")[0] for u in envelope["unreadable"]] == ["clippy.toml"]

    def test_conf_dir_makes_the_lookup_unknowable(self, tmp_path: pathlib.Path) -> None:
        """`CLIPPY_CONF_DIR` in `.cargo/config.toml` is reported, not guessed."""
        envelope = _build(
            tmp_path,
            {
                "Cargo.toml": PACKAGE,
                ".cargo/config.toml": '[env]\nCLIPPY_CONF_DIR = "ci"\n',
            },
        )
        assert any("CLIPPY_CONF_DIR" in u for u in envelope["unreadable"])


class TestWrappers:
    """Wrapper crates a member depends on bring their own required paths."""

    def _wrappers(
        self, tmp_path: pathlib.Path, manifest: str, extra: dict[str, str] | None = None
    ) -> list[tuple[str, list[str]]]:
        """Return `(crate, paths)` for the root member of *manifest*."""
        envelope = _build(tmp_path, {"Cargo.toml": manifest, **(extra or {})})
        return [(w["crate"], w["paths"]) for w in envelope["members"][0]["wrappers"]]

    def test_dependency_tables_and_targets(self, tmp_path: pathlib.Path) -> None:
        """Every dependency table counts, including a target's."""
        found = self._wrappers(
            tmp_path,
            PACKAGE
            + '[build-dependencies]\ntemp-env = "0.3"\n'
            + "[target.'cfg(unix)'.dev-dependencies]\nenv-lock = \"1\"\n",
        )
        assert [crate for crate, _ in found] == ["env_lock", "temp_env"]

    def test_renamed_dependency_uses_the_package_name(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A rename does not hide the crate."""
        found = self._wrappers(
            tmp_path,
            PACKAGE
            + '[dev-dependencies]\nenvy = { package = "temp-env", version = "0.3" }\n',
        )
        assert [crate for crate, _ in found] == ["temp_env"]

    def test_workspace_inherited_features(self, tmp_path: pathlib.Path) -> None:
        """`workspace = true` takes the features the workspace entry enables."""
        envelope = _build(
            tmp_path,
            {
                "Cargo.toml": (
                    '[workspace]\nmembers = ["a"]\n[workspace.dependencies]\n'
                    'temp-env = { version = "0.3", features = ["async_closure"] }\n'
                ),
                "a/Cargo.toml": PACKAGE
                + "[dev-dependencies]\ntemp-env = { workspace = true }\n",
            },
        )
        [wrapper] = envelope["members"][0]["wrappers"]
        assert "temp_env::async_with_vars" in wrapper["paths"]

    def test_feature_gated_paths_need_the_feature(self, tmp_path: pathlib.Path) -> None:
        """Without `async_closure` the async entry point does not exist."""
        [(_, paths)] = self._wrappers(
            tmp_path, PACKAGE + '[dev-dependencies]\ntemp-env = "0.3"\n'
        )
        assert "temp_env::async_with_vars" not in paths

    def test_figment_needs_the_test_feature(self, tmp_path: pathlib.Path) -> None:
        """`Jail` exists only behind figment's `test` feature."""
        plain = self._wrappers(
            tmp_path, PACKAGE + '[dev-dependencies]\nfigment = "0.10"\n'
        )
        assert plain == []

    def test_figment_with_the_test_feature(self, tmp_path: pathlib.Path) -> None:
        """With the feature, `Jail`'s entry points are required."""
        [(crate, paths)] = self._wrappers(
            tmp_path,
            PACKAGE
            + "[dev-dependencies]\n"
            + 'figment = { version = "0.10", features = ["test"] }\n',
        )
        assert crate == "figment"
        assert "figment::Jail::expect_with" in paths

    def test_default_feature_forwarding(self, tmp_path: pathlib.Path) -> None:
        """A default feature that forwards `figment/test` enables `Jail`."""
        manifest = (
            PACKAGE
            + '[features]\ndefault = ["extras"]\nextras = ["figment/test"]\n'
            + '[dev-dependencies]\nfigment = "0.10"\n'
        )
        assert [crate for crate, _ in self._wrappers(tmp_path, manifest)] == ["figment"]

    def test_forwarding_that_is_not_default_is_ignored(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A feature nobody enables by default does not enable the wrapper."""
        manifest = (
            PACKAGE
            + '[features]\nextras = ["figment/test"]\n'
            + '[dev-dependencies]\nfigment = "0.10"\n'
        )
        assert self._wrappers(tmp_path, manifest) == []

    def test_optional_forwarding_and_async_closure(
        self, tmp_path: pathlib.Path
    ) -> None:
        """`dep?/feature` forwards too, here `temp-env`'s async closures."""
        manifest = (
            PACKAGE
            + '[features]\ndefault = ["temp-env?/async_closure"]\n'
            + '[dev-dependencies]\ntemp-env = { version = "0.3", optional = true }\n'
        )
        [(_, paths)] = self._wrappers(tmp_path, manifest)
        assert "temp_env::async_with_vars" in paths

    def test_wrapper_table_is_a_parameter(self, tmp_path: pathlib.Path) -> None:
        """The manifest's table replaces the default."""
        envelope = _build(
            tmp_path,
            {"Cargo.toml": PACKAGE + '[dev-dependencies]\nserial-env = "1"\n'},
            {"wrapper_paths": {"serial_env": {"paths": ["serial_env::set"]}}},
        )
        assert envelope["members"][0]["wrappers"] == [
            {"crate": "serial_env", "paths": ["serial_env::set"]}
        ]


class TestSuppressions:
    """Source attributes are read with a parser, member by member."""

    def _lib(self, tmp_path: pathlib.Path, source: str) -> list[tuple[str, int, str]]:
        """Return the suppressions found in a single-file crate."""
        return _kinds(_build(tmp_path, {"Cargo.toml": PACKAGE, "src/lib.rs": source}))

    @pytest.mark.parametrize(
        "source",
        [
            f"{EXPECT}fn f() {{}}\n",
            f"fn f() {{\n    {EXPECT}    let _x = 1;\n}}\n",
            f"fn f() {{\n    {EXPECT}    g();\n}}\n",
            f"{EXPECT}impl T for P {{}}\n",
            f"{EXPECT}const C: u8 = 0;\n",
            "// #[allow(clippy::disallowed_methods)]\nfn f() {}\n",
            'const S: &str = "#![allow(clippy::all)]";\n',
            "#[allow(dead_code)]\nfn f() {}\n",
            f"#[inline]\n{EXPECT}fn f() {{}}\n",
            '#[expect(clippy::disallowed_methods, reason = r#"adapter"#)]\nfn f() {}\n',
            (
                '#[expect(clippy::disallowed_methods, reason = "one line \\\n'
                '    and another")]\nfn f() {}\n'
            ),
            _macro(EXPECT),
            "macro_rules! m { () => { #[allow(dead_code)] fn g() {} }; }\n",
        ],
    )
    def test_sanctioned_and_unrelated_forms(
        self, tmp_path: pathlib.Path, source: str
    ) -> None:
        """Site-level expectations, comments, strings and other lints are quiet."""
        assert self._lib(tmp_path, source) == []

    @pytest.mark.parametrize(
        ("source", "kind"),
        [
            ("#[allow(clippy::disallowed_methods)]\nfn f() {}\n", "allow"),
            ("#![allow(clippy::disallowed_methods)]\n", "allow"),
            ("#![allow(clippy::style)]\n", "allow"),
            ("#[allow(dead_code, clippy::all)]\nfn f() {}\n", "allow"),
            (
                "#[cfg_attr(test, allow(clippy::disallowed_methods))]\nfn f() {}\n",
                "allow",
            ),
            ("#[expect(clippy::disallowed_methods)]\nfn f() {}\n", "expect-no-reason"),
            (
                '#[expect(clippy::disallowed_methods, reason = " ")]\nfn f() {}\n',
                "expect-no-reason",
            ),
            (
                "#[expect(clippy::disallowed_methods, reason = 3)]\nfn f() {}\n",
                "expect-no-reason",
            ),
            ('#![expect(clippy::disallowed_methods, reason = "x")]\n', "expect-scope"),
            (f"{EXPECT}mod m {{}}\n", "expect-scope"),
            (f"{EXPECT}trait T {{}}\n", "expect-scope"),
            ('#[expect(clippy::all, reason = "x")]\nfn f() {}\n', "expect-scope"),
            ("#![allow(warnings)]\n", "allow"),
            ("#[allow(warnings)]\nfn f() {}\n", "allow"),
            (_macro("#[allow(clippy::disallowed_methods)]"), "allow"),
            ("m! { #![allow(warnings)] }\n", "allow"),
            (_macro("#[expect(clippy::disallowed_methods)]"), "expect-no-reason"),
            (
                '#[expect(clippy::disallowed_methods, reason = r"")]\nfn f() {}\n',
                "expect-no-reason",
            ),
        ],
    )
    def test_each_kind(self, tmp_path: pathlib.Path, source: str, kind: str) -> None:
        """Each way of silencing the lint is reported once, with its kind."""
        assert self._lib(tmp_path, source) == [("src/lib.rs", 1, kind)]

    def test_nested_cfg_attr_expect_is_still_judged(
        self, tmp_path: pathlib.Path
    ) -> None:
        """An `expect` wrapped in `cfg_attr` follows the same rules."""
        source = "#[cfg_attr(test, expect(clippy::disallowed_methods))]\nfn f() {}\n"
        assert self._lib(tmp_path, source) == [("src/lib.rs", 1, "expect-no-reason")]

    def test_tests_benches_and_build_scripts_are_read(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Sites live in tests, so the walk does not skip them."""
        allow = "#![allow(clippy::disallowed_methods)]\n"
        envelope = _build(
            tmp_path,
            {
                "Cargo.toml": PACKAGE,
                "tests/it.rs": allow,
                "benches/b.rs": allow,
                "build.rs": allow,
            },
        )
        assert [s["path"] for s in envelope["suppressions"]] == [
            "benches/b.rs",
            "build.rs",
            "tests/it.rs",
        ]

    def test_other_packages_are_not_read(self, tmp_path: pathlib.Path) -> None:
        """Fixtures, vendored code, build output and non-member crates are skipped."""
        allow = "#![allow(clippy::disallowed_methods)]\n"
        envelope = _build(
            tmp_path,
            {
                "Cargo.toml": PACKAGE,
                "tests/fixtures/probe/src/lib.rs": allow,
                "vendor/dep/src/lib.rs": allow,
                "target/debug/build/x.rs": allow,
                "examples/ws/Cargo.toml": PACKAGE,
                "examples/ws/src/lib.rs": allow,
            },
        )
        assert envelope["suppressions"] == []

    def test_a_member_nested_in_the_root_package_is_read_once(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A member below the root package is not counted twice."""
        envelope = _build(
            tmp_path,
            {
                "Cargo.toml": PACKAGE + '[workspace]\nmembers = ["inner"]\n',
                "inner/Cargo.toml": PACKAGE,
                "inner/src/lib.rs": "#![allow(clippy::all)]\n",
            },
        )
        assert [s["path"] for s in envelope["suppressions"]] == ["inner/src/lib.rs"]

    def test_a_linked_file_is_not_followed(self, tmp_path: pathlib.Path) -> None:
        """A symbolic link is not read, so nothing outside the crate is judged."""
        elsewhere = tmp_path / "elsewhere.rs"
        elsewhere.write_text("#![allow(clippy::all)]\n", encoding="utf-8")
        checkout = tmp_path / "checkout"
        _lay_out(checkout, {"Cargo.toml": PACKAGE})
        (checkout / "src").mkdir()
        (checkout / "src" / "lib.rs").symlink_to(elsewhere)
        assert build_test_environment_envelope(checkout)["suppressions"] == []

    def test_syntax_error_naming_the_lint_is_unreadable(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A parse failure around the lint is not evidence of absence."""
        envelope = _build(
            tmp_path,
            {
                "Cargo.toml": PACKAGE,
                "src/lib.rs": "#[allow(clippy::disallowed_methods\nfn f( {\n",
            },
        )
        assert [u.split(":")[0] for u in envelope["unreadable"]] == ["src/lib.rs"]

    def test_syntax_error_elsewhere_is_ignored(self, tmp_path: pathlib.Path) -> None:
        """Syntax the parser predates cannot hide a suppression of this lint."""
        assert self._lib(tmp_path, "fn ok() {}\nlet let = ;;\n") == []


class TestConfigurationSuppressions:
    """Settings outside source that lower the lint."""

    def _details(self, tmp_path: pathlib.Path, files: dict[str, str]) -> list[str]:
        """Return the recorded details for a checkout."""
        return [c["detail"] for c in _build(tmp_path, files)["config_suppressions"]]

    @pytest.mark.parametrize(
        "manifest",
        [
            PACKAGE + '[lints.clippy]\ndisallowed_methods = "allow"\n',
            PACKAGE + '[lints.clippy]\nstyle = { level = "allow", priority = -1 }\n',
            PACKAGE + '[workspace.lints.clippy]\nall = "allow"\n',
            PACKAGE + '[lints.rust]\nwarnings = "allow"\n',
        ],
    )
    def test_lints_tables(self, tmp_path: pathlib.Path, manifest: str) -> None:
        """An `allow` of the lint or a group in a lints table is reported."""
        assert len(self._details(tmp_path, {"Cargo.toml": manifest})) == 1

    def test_deny_and_other_lints_are_quiet(self, tmp_path: pathlib.Path) -> None:
        """Raising the lint, or allowing another, is fine."""
        manifest = (
            PACKAGE
            + '[lints.clippy]\ndisallowed_methods = "deny"\ndbg_macro = "allow"\n'
        )
        assert self._details(tmp_path, {"Cargo.toml": manifest}) == []

    @pytest.mark.parametrize(
        "config",
        [
            '[build]\nrustflags = ["-A", "clippy::disallowed_methods"]\n',
            '[build]\nrustflags = ["-Aclippy::style"]\n',
            '[target.x]\nrustflags = "--allow=clippy::all"\n',
            '[env]\nCLIPPY_ARGS = "--allow clippy::disallowed_methods"\n',
            '[env]\nRUSTFLAGS = { value = "-A clippy::all", force = true }\n',
            '[build]\nrustflags = ["-Awarnings"]\n',
        ],
    )
    def test_flags(self, tmp_path: pathlib.Path, config: str) -> None:
        """Each spelling of an allow flag is reported."""
        found = self._details(
            tmp_path, {"Cargo.toml": PACKAGE, ".cargo/config.toml": config}
        )
        assert len(found) == 1

    def test_unrelated_flags_are_quiet(self, tmp_path: pathlib.Path) -> None:
        """Flags that do not allow a silencing lint are ignored."""
        config = '[build]\nrustflags = ["-C", "target-cpu=native", "-A", "dead_code"]\n'
        assert (
            self._details(
                tmp_path, {"Cargo.toml": PACKAGE, ".cargo/config.toml": config}
            )
            == []
        )

    def test_invalid_cargo_manifest_is_unreadable(self, tmp_path: pathlib.Path) -> None:
        """A manifest that is not TOML is reported, and no member is invented."""
        envelope = _build(tmp_path, {"Cargo.toml": "[package\n"})
        assert envelope["members"] == []
        assert [u.split(":")[0] for u in envelope["unreadable"]] == ["Cargo.toml"]
