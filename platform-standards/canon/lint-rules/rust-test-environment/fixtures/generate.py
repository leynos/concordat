"""Regenerate the policy-input fixtures for rust-test-environment.

Each scenario lays out a synthetic checkout from the file contents written
below, then hands it to the production ``build_test_environment_envelope`` so
the recorded envelopes are exactly what ``concordat artefact rule run`` would
send to Conftest. ``data.json`` bundles every envelope under a ``fixtures`` key
for ``conftest verify``, beside the manifest's default parameters.

Run from the repository root::

    uv run python \
        platform-standards/canon/lint-rules/rust-test-environment/fixtures/generate.py
"""

from __future__ import annotations

import json
import tempfile
import typing as typ
from pathlib import Path

from ruamel.yaml import YAML

from concordat.rules.test_environment_envelope import (
    TestEnvironmentEnvelope,
    build_test_environment_envelope,
)

FIXTURES_DIR = Path(__file__).resolve().parent
MANIFEST_PATH = FIXTURES_DIR.parent / "rule.yaml"

# The recorded repository path must not leak the temporary directory the
# scenario was laid out in; every envelope names the checkout as `.`.
RECORDED_REPOSITORY_PATH: typ.Final = "."

CLIPPY_TOML = """\
disallowed-methods = [
  { path = "std::env::set_var", reason = "use a stub environment in tests" },
  { path = "std::env::remove_var", reason = "use a stub environment in tests" },
  { path = "std::env::set_current_dir", reason = "pass the directory in" },
]
"""
CLIPPY_NO_CWD = """\
disallowed-methods = [
  "std::env::set_var",
  "std::env::remove_var",
]
"""
CLIPPY_WITH_TEMP_ENV = CLIPPY_TOML + ""
CLIPPY_WRAPPED = """\
disallowed-methods = [
  "std::env::set_var",
  "std::env::remove_var",
  "std::env::set_current_dir",
  "temp_env::with_var",
  "temp_env::with_var_unset",
  "temp_env::with_vars",
  "temp_env::with_vars_unset",
]
"""
CARGO = '[package]\nname = "demo"\nversion = "0.1.0"\nedition = "2024"\n'
EXPECT = '#[expect(clippy::disallowed_methods, reason = "adapter behind the seam")]\n'
LIB = f"{EXPECT}fn adapter() {{}}\n"


def cargo(extra: str = "") -> str:
    """Return a package manifest with *extra* TOML appended.

    Returns
    -------
    str
        The manifest text.
    """
    return CARGO + extra


def workspace(*members: str) -> str:
    """Return a virtual workspace manifest naming *members*.

    Returns
    -------
    str
        The manifest text.
    """
    listed = ", ".join(f'"{m}"' for m in members)
    return f'[workspace]\nmembers = [{listed}]\nresolver = "3"\n'


def crate(name: str) -> dict[str, str | None]:
    """Return the files of a minimal member crate called *name*.

    Returns
    -------
    dict[str, str | None]
        Paths and contents, relative to the checkout.
    """
    return {
        f"crates/{name}/Cargo.toml": f'[package]\nname = "{name}"\nversion = "0.1.0"\n',
        f"crates/{name}/src/lib.rs": "pub fn run() {}\n",
    }


def with_dependency(spec: str) -> str:
    """Return a package manifest with one `[dev-dependencies]` line.

    Returns
    -------
    str
        The manifest text.
    """
    return cargo(f"\n[dev-dependencies]\n{spec}\n")


# Every scenario starts from this checkout; a scenario's files overlay it and a
# value of ``None`` removes the file.
BASE: typ.Final[dict[str, str | None]] = {
    "Cargo.toml": CARGO,
    "clippy.toml": CLIPPY_TOML,
    "src/lib.rs": LIB,
    "tests/it.rs": "#[test]\nfn it() {}\n",
}

SCENARIOS: typ.Final[dict[str, dict[str, str | None]]] = {
    "compliant": {},
    "compliant_dotted_config": {"clippy.toml": None, ".clippy.toml": CLIPPY_TOML},
    "compliant_string_entries": {
        "clippy.toml": (
            'disallowed-methods = ["std::env::set_var", "std::env::remove_var",'
            ' "std::env::set_current_dir"]\n'
        )
    },
    "compliant_expect_on_statement": {
        "tests/it.rs": (
            "#[test]\nfn it() {\n"
            f"    {EXPECT}"
            '    let _ = unsafe { std::env::set_var("A", "b") };\n'
            f"    {EXPECT}"
            "    helper();\n}\n"
        )
    },
    "compliant_expect_on_impl_adapter": {
        "src/lib.rs": f"{EXPECT}impl Env for Process {{}}\n"
    },
    "compliant_comments_and_strings": {
        "src/lib.rs": (
            "// #[allow(clippy::disallowed_methods)]\n"
            "/// ```\n/// #![allow(clippy::disallowed_methods)]\n/// ```\n"
            'const S: &str = "#[allow(clippy::disallowed_methods)]";\n'
        )
    },
    "compliant_other_lint_allowed": {
        "src/lib.rs": "#[allow(clippy::too_many_arguments)]\nfn f() {}\n"
    },
    "compliant_syntax_error_elsewhere": {"src/lib.rs": "fn ok() {}\nlet let = ;;\n"},
    "compliant_workspace": {
        "Cargo.toml": workspace("crates/a", "crates/b"),
        "src/lib.rs": None,
        "tests/it.rs": None,
        **crate("a"),
        **crate("b"),
    },
    "compliant_member_own_config": {
        "Cargo.toml": workspace("crates/*"),
        "src/lib.rs": None,
        "tests/it.rs": None,
        "crates/a/clippy.toml": CLIPPY_TOML,
        **crate("a"),
    },
    "compliant_unlisted_fixture_crate": {
        "tests/fixtures/probe/Cargo.toml": CARGO,
        "tests/fixtures/probe/src/lib.rs": (
            "#![allow(clippy::disallowed_methods)]\n"
            'fn f() { std::env::set_var("A", "b") }\n'
        ),
        "nested/Cargo.toml": CARGO,
        "nested/src/lib.rs": "#![allow(clippy::disallowed_methods)]\n",
    },
    "compliant_wrapper_disallowed": {
        "Cargo.toml": with_dependency('temp-env = "0.3"'),
        "clippy.toml": CLIPPY_WRAPPED,
    },
    "compliant_figment_without_test_feature": {
        "Cargo.toml": with_dependency('figment = "0.10"')
    },
    "no_cargo_manifest": {"Cargo.toml": None},
    "no_clippy_config": {"clippy.toml": None},
    "config_without_disallowed_methods": {
        "clippy.toml": "cognitive-complexity-threshold = 8\n"
    },
    "missing_set_current_dir": {"clippy.toml": CLIPPY_NO_CWD},
    "member_config_shadows_root": {
        "Cargo.toml": workspace("crates/a"),
        "src/lib.rs": None,
        "tests/it.rs": None,
        "crates/a/clippy.toml": CLIPPY_NO_CWD,
        **crate("a"),
    },
    "member_without_inherited_config": {
        "Cargo.toml": workspace("crates/a"),
        "clippy.toml": None,
        "src/lib.rs": None,
        "tests/it.rs": None,
        **crate("a"),
    },
    "workspace_sharing_an_incomplete_config": {
        "Cargo.toml": workspace("crates/a", "crates/b"),
        "clippy.toml": CLIPPY_NO_CWD,
        "src/lib.rs": None,
        "tests/it.rs": None,
        **crate("a"),
        **crate("b"),
    },
    "wrapper_not_disallowed": {"Cargo.toml": with_dependency('temp-env = "0.3"')},
    "wrapper_renamed": {
        "Cargo.toml": with_dependency(
            'envy = { package = "temp-env", version = "0.3" }'
        )
    },
    "wrapper_inherited_from_workspace": {
        "Cargo.toml": (
            '[workspace]\nmembers = ["crates/a"]\n[workspace.dependencies]\n'
            'temp-env = { version = "0.3", features = ["async_closure"] }\n'
        ),
        "clippy.toml": CLIPPY_WRAPPED,
        "src/lib.rs": None,
        "tests/it.rs": None,
        "crates/a/Cargo.toml": (
            '[package]\nname = "a"\nversion = "0.1.0"\n'
            "[dev-dependencies]\ntemp-env = { workspace = true }\n"
        ),
        "crates/a/src/lib.rs": "pub fn run() {}\n",
    },
    "wrapper_target_table": {
        "Cargo.toml": cargo(
            "\n[target.'cfg(unix)'.dev-dependencies]\nenv-lock = \"1\"\n"
        )
    },
    "wrapper_figment_test_feature": {
        "Cargo.toml": with_dependency(
            'figment = { version = "0.10", features = ["test"] }'
        )
    },
    "allow_on_function": {
        "src/lib.rs": "#[allow(clippy::disallowed_methods)]\nfn f() {}\n"
    },
    "allow_crate_level": {"src/lib.rs": "#![allow(clippy::disallowed_methods)]\n"},
    "allow_in_cfg_attr": {
        "tests/it.rs": (
            "#[cfg_attr(test, allow(clippy::disallowed_methods))]\n"
            "#[test]\nfn it() {}\n"
        )
    },
    "allow_group_style": {"src/lib.rs": "#![allow(clippy::style)]\n"},
    "allow_group_all_in_a_list": {
        "src/lib.rs": "#[allow(dead_code, clippy::all)]\nfn f() {}\n"
    },
    "expect_without_reason": {
        "src/lib.rs": "#[expect(clippy::disallowed_methods)]\nfn f() {}\n"
    },
    "expect_blank_reason": {
        "src/lib.rs": (
            '#[expect(clippy::disallowed_methods, reason = "  ")]\nfn f() {}\n'
        )
    },
    "expect_crate_level": {
        "src/lib.rs": (
            '#![expect(clippy::disallowed_methods, reason = "all of it")]\nfn f() {}\n'
        )
    },
    "expect_on_module": {
        "src/lib.rs": f"{EXPECT}mod helpers {{}}\n",
    },
    "expect_on_group": {
        "src/lib.rs": '#[expect(clippy::style, reason = "x")]\nfn f() {}\n'
    },
    "cargo_lints_allow": {
        "Cargo.toml": cargo('\n[lints.clippy]\ndisallowed_methods = "allow"\n')
    },
    "cargo_lints_table_form": {
        "Cargo.toml": cargo(
            '\n[lints.clippy]\nstyle = { level = "allow", priority = 1 }\n'
        )
    },
    "cargo_workspace_lints": {
        "Cargo.toml": (CARGO + '[workspace.lints.clippy]\nall = "allow"\n')
    },
    "cargo_lints_deny_is_fine": {
        "Cargo.toml": cargo('\n[lints.clippy]\ndisallowed_methods = "deny"\n')
    },
    "cargo_config_rustflags": {
        ".cargo/config.toml": (
            '[build]\nrustflags = ["-A", "clippy::disallowed_methods"]\n'
        )
    },
    "cargo_config_joined_flag": {
        ".cargo/config.toml": (
            '[target.x86_64-unknown-linux-gnu]\nrustflags = "-Aclippy::style"\n'
        )
    },
    "cargo_config_unrelated_flag": {
        ".cargo/config.toml": '[build]\nrustflags = ["-C", "target-cpu=native"]\n'
    },
    "clippy_conf_dir_set": {".cargo/config.toml": '[env]\nCLIPPY_CONF_DIR = "ci"\n'},
    "clippy_toml_invalid": {"clippy.toml": "disallowed-methods = [\n"},
    "cargo_manifest_invalid": {"Cargo.toml": "[package\nname = \n"},
    "syntax_error_names_the_lint": {
        "src/lib.rs": "#[allow(clippy::disallowed_methods\nfn f( {\n"
    },
    "test_file_allow_is_read": {
        "tests/it.rs": "#![allow(clippy::disallowed_methods)]\n#[test]\nfn it() {}\n"
    },
}


def lay_out(files: dict[str, str | None], checkout: Path) -> None:
    """Populate *checkout* with the base files, overlaid by *files*.

    Parameters
    ----------
    files:
        The scenario's files; ``None`` removes a base file.
    checkout:
        An existing empty directory to lay the files out in.
    """
    for relative, text in {**BASE, **files}.items():
        if text is None:
            continue
        path = checkout / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def manifest_parameters() -> dict[str, object]:
    """Return the manifest defaults the rule runs with.

    Returns
    -------
    dict[str, object]
        The ``defaults`` of ``rule.yaml``.
    """
    manifest = YAML(typ="safe").load(MANIFEST_PATH.read_text(encoding="utf-8"))
    return dict(manifest["parameters"]["defaults"])


def build_fixture_envelope(
    files: dict[str, str | None], parameters: dict[str, object]
) -> TestEnvironmentEnvelope:
    """Return the production envelope for a scenario, with a stable path.

    Returns
    -------
    TestEnvironmentEnvelope
        The recorded envelope, with the checkout path rewritten to ``.`` so the
        temporary directory does not leak into version control.
    """
    with tempfile.TemporaryDirectory(prefix="test-environment-fixture-") as scratch:
        checkout = Path(scratch)
        lay_out(files, checkout)
        envelope = build_test_environment_envelope(checkout, parameters)
    envelope["repository"]["path"] = RECORDED_REPOSITORY_PATH
    return envelope


def main() -> None:
    """Regenerate ``data.json``, the bundle ``conftest verify --data`` consumes."""
    parameters = manifest_parameters()
    envelopes = {
        key: build_fixture_envelope(files, parameters)
        for key, files in SCENARIOS.items()
    }
    bundle = FIXTURES_DIR / "data.json"
    bundle.write_text(
        json.dumps({"fixtures": envelopes, "parameters": parameters}, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
