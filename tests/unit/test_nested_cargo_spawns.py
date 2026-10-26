"""Read the cargo spawns and trybuild uses inside a checkout's test scope.

The rule is a text-level approximation of a Rust compiler, so every case here
is either a shape the corpus pins — whitaker's literal spawns, theoremc's
`cargo_run_output` helper — or an evidence form the policy distinguishes:
`offline`, `online`, and the `unresolved` reading that says a source could not
be established rather than guessing either way.
"""

from __future__ import annotations

import dataclasses
import typing as typ

import pytest

if typ.TYPE_CHECKING:
    import pathlib

from concordat.rules.nested_cargo_spawns import (
    collect_rust_evidence,
    collect_trybuild_manifests,
    fixture_directories,
)

if typ.TYPE_CHECKING:
    from concordat.rules.nested_cargo import Manifest
    from concordat.rules.nested_cargo_spawns import RustEvidence


def put(root: pathlib.Path, relative: str, text: str) -> None:
    """Write one file, creating its directory."""
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def evidence(root: pathlib.Path, problems: list[str] | None = None) -> RustEvidence:
    """Read the evidence for a checkout whose manifests are already written."""
    from concordat.rules.nested_cargo import load_manifests

    collected = [] if problems is None else problems
    manifests = load_manifests(root, collected)
    return collect_rust_evidence(root, manifests, collected)


def states(root: pathlib.Path) -> list[tuple[str, str]]:
    """Return the `(form, state)` of every spawn fact, in fact order."""
    return [(fact["form"], fact["state"]) for fact in evidence(root).spawns]


@dataclasses.dataclass(frozen=True, slots=True)
class Case:
    """One Rust file and the spawn facts it must produce."""

    source: str
    facts: tuple[tuple[str, str], ...]


CASES: dict[str, Case] = {
    "literal-online": Case(
        'fn run() {\n    Command::new("cargo").arg("build").status();\n}\n',
        (("command_new", "online"),),
    ),
    "frozen": Case(
        'fn run() {\n    Command::new("cargo").args(["build", "--frozen"]);\n}\n',
        (("command_new", "offline"),),
    ),
    "offline-and-locked": Case(
        'fn run() {\n    Command::new("cargo").args(["--offline", "--locked"]);\n}\n',
        (("command_new", "offline"),),
    ),
    "offline-alone": Case(
        'fn run() {\n    Command::new("cargo").arg("--offline");\n}\n',
        (("command_new", "online"),),
    ),
    "command-local-env": Case(
        "fn run() {\n"
        '    Command::new("cargo")\n'
        '        .env("CARGO_NET_OFFLINE", "true")\n'
        '        .arg("build");\n'
        "}\n",
        (("command_new", "offline"),),
    ),
    "literal-envs": Case(
        "fn run() {\n"
        '    Command::new("cargo").envs([("CARGO_NET_OFFLINE", "true")]);\n'
        "}\n",
        (("command_new", "offline"),),
    ),
    "inherited-environment": Case(
        'fn run() {\n    std::env::set_var("CARGO_NET_OFFLINE", "true");\n'
        '    Command::new("cargo").arg("build");\n}\n',
        (("command_new", "online"),),
    ),
    "env-macro": Case(
        'fn run() {\n    Command::new(env!("CARGO")).arg("build");\n}\n',
        (("env_cargo", "online"),),
    ),
    "option-env-unwrap": Case(
        "fn run() {\n"
        '    Command::new(option_env!("CARGO").expect("cargo")).arg("build");\n'
        "}\n",
        (("env_cargo", "online"),),
    ),
    "env-var-unwrap": Case(
        "fn run() {\n"
        '    Command::new(std::env::var("CARGO").unwrap()).arg("build");\n'
        "}\n",
        (("env_cargo", "online"),),
    ),
    "env-var-os": Case(
        "fn run() {\n"
        '    Command::new(env::var_os("CARGO").expect("cargo")).arg("build");\n'
        "}\n",
        (("env_cargo", "online"),),
    ),
    "not-cargo": Case(
        'fn run() {\n    Command::new("rustc").arg("--version");\n}\n',
        (),
    ),
    "non-literal-argument": Case(
        "fn run(args: &[&str]) {\n"
        '    Command::new("cargo").args(args).arg("build");\n'
        "}\n",
        (("command_new", "unresolved"),),
    ),
    "non-literal-environment": Case(
        "fn run(key: &str) {\n"
        '    Command::new("cargo").env(key, "true").arg("build");\n'
        "}\n",
        (("command_new", "unresolved"),),
    ),
    "version": Case(
        'fn run() {\n    Command::new("cargo").arg("--version");\n}\n',
        (),
    ),
    "short-version": Case(
        'fn run() {\n    Command::new("cargo").arg("-V");\n}\n',
        (),
    ),
    "help": Case(
        'fn run() {\n    Command::new("cargo").args(["help"]);\n}\n',
        (),
    ),
    "mentions-only": Case(
        '// Command::new("cargo").arg("build")\n'
        'const TEXT: &str = "Command::new(\\"cargo\\")";\n',
        (),
    ),
    "bound-variable": Case(
        'fn run() {\n    let mut command = Command::new("cargo");\n'
        '    command.arg("build");\n    command.status();\n}\n',
        (("command_new", "online"),),
    ),
    "bound-variable-offline": Case(
        'fn run() {\n    let mut command = Command::new("cargo");\n'
        '    command.arg("build");\n    command.arg("--frozen");\n}\n',
        (("command_new", "offline"),),
    ),
    "helper-online": Case(
        'fn cargo() -> Command {\n    Command::new("cargo")\n}\n\n'
        'fn run() {\n    cargo().arg("test").status();\n}\n',
        (("helper", "online"),),
    ),
    "helper-offline": Case(
        'fn cargo() -> Command {\n    Command::new("cargo").arg("--frozen")\n}\n\n'
        'fn run() {\n    cargo().arg("test").status();\n}\n',
        (),
    ),
    "helper-call-site-offline": Case(
        'fn cargo() -> Command {\n    Command::new("cargo")\n}\n\n'
        'fn run() {\n    cargo().arg("--frozen").status();\n}\n',
        (),
    ),
    "helper-bound-call-site": Case(
        'fn cargo() -> Command {\n    Command::new("cargo")\n}\n\n'
        "fn run() {\n    let mut command = cargo();\n"
        '    command.arg("build");\n    command.status();\n}\n',
        (("helper", "online"),),
    ),
    # whitaker's `common/tests/i18n_packaging.rs` sets an unrelated variable to
    # a computed value. That says nothing about the network, so the spawn keeps
    # the literal reading it has.
    "unrelated-non-literal-environment": Case(
        'fn run(target: &Path) {\n    Command::new("cargo")\n'
        '        .env("CARGO_TARGET_DIR", target)\n        .arg("build");\n}\n',
        (("command_new", "online"),),
    ),
    # theoremc's `cargo_run_output` forwards an argument list it was handed, so
    # what it passes really is unknown.
    "forwarded-arguments": Case(
        'fn run(subcommand: &Subcommand) {\n    Command::new("cargo")\n'
        "        .args(subcommand.args())\n        .output();\n}\n",
        (("command_new", "unresolved"),),
    ),
}


@pytest.mark.parametrize("case", CASES.values(), ids=CASES.keys())
def test_spawns_are_classified(case: Case, tmp_path: pathlib.Path) -> None:
    """Each evidence form carries the state the policy acts on."""
    put(tmp_path, "tests/spawn.rs", case.source)
    assert tuple(states(tmp_path)) == case.facts


def test_a_spawn_outside_test_scope_is_ignored(tmp_path: pathlib.Path) -> None:
    """Library code building cargo is not a nested test build."""
    source = 'fn run() {\n    Command::new("cargo").arg("build");\n}\n'
    put(tmp_path, "src/lib.rs", source)
    assert states(tmp_path) == []


def test_an_inline_cfg_test_module_is_test_scope(tmp_path: pathlib.Path) -> None:
    """A module that only exists in test builds is test code."""
    put(
        tmp_path,
        "src/lib.rs",
        "#[cfg(test)]\nmod tests {\n    fn run() {\n"
        '        Command::new("cargo").arg("build");\n    }\n}\n',
    )
    assert states(tmp_path) == [("command_new", "online")]


def test_a_cfg_all_test_module_is_test_scope(tmp_path: pathlib.Path) -> None:
    """`cfg(all(test, unix))` still only exists in a test build."""
    put(
        tmp_path,
        "src/lib.rs",
        "#[cfg(all(test, unix))]\nmod tests {\n    fn run() {\n"
        '        Command::new("cargo").arg("build");\n    }\n}\n',
    )
    assert states(tmp_path) == [("command_new", "online")]


def test_a_not_test_module_is_not_test_scope(tmp_path: pathlib.Path) -> None:
    """`cfg(not(test))` is exactly the code a test build leaves out."""
    put(
        tmp_path,
        "src/lib.rs",
        "#[cfg(not(test))]\nmod build {\n    fn run() {\n"
        '        Command::new("cargo").arg("build");\n    }\n}\n',
    )
    assert states(tmp_path) == []


def test_an_out_of_line_cfg_test_module_is_test_scope(tmp_path: pathlib.Path) -> None:
    """`mod tests;` resolves to the sibling file, which is then test scope."""
    put(tmp_path, "src/lib.rs", "#[cfg(test)]\nmod tests;\n")
    put(
        tmp_path,
        "src/tests.rs",
        'fn run() {\n    Command::new("cargo").arg("build");\n}\n',
    )
    facts = evidence(tmp_path).spawns
    assert [(fact["path"], fact["form"], fact["state"]) for fact in facts] == [
        ("src/tests.rs", "command_new", "online")
    ]


def test_an_out_of_line_module_directory_is_test_scope(tmp_path: pathlib.Path) -> None:
    """`mod tests;` may also name `tests/mod.rs`."""
    put(tmp_path, "src/lib.rs", "#[cfg(test)]\nmod tests;\n")
    put(
        tmp_path,
        "src/tests/mod.rs",
        'fn run() {\n    Command::new("cargo").arg("build");\n}\n',
    )
    assert [(fact["form"], fact["state"]) for fact in evidence(tmp_path).spawns] == [
        ("command_new", "online")
    ]


def test_an_out_of_line_module_with_a_path_attribute_is_test_scope(
    tmp_path: pathlib.Path,
) -> None:
    """`#[path = "..."]` names the file instead of the module name.

    whitaker writes its test modules this way, so resolving only `tests.rs`
    would leave every one of them unread.
    """
    put(
        tmp_path,
        "src/workspace.rs",
        '#[cfg(test)]\n#[path = "workspace_tests.rs"]\nmod tests;\n',
    )
    put(
        tmp_path,
        "src/workspace_tests.rs",
        'fn run() {\n    Command::new("cargo").arg("build");\n}\n',
    )
    facts = evidence(tmp_path).spawns
    assert [(fact["path"], fact["state"]) for fact in facts] == [
        ("src/workspace_tests.rs", "online")
    ]


def test_a_file_with_a_char_literal_quote_is_readable(tmp_path: pathlib.Path) -> None:
    """`trim_matches('"')` is a char literal, not the start of a string."""
    put(
        tmp_path,
        "tests/clean.rs",
        "fn trim(text: &str) -> &str {\n    text.trim_matches('\"')\n}\n",
    )
    problems: list[str] = []
    evidence(tmp_path, problems)
    assert problems == []


def test_an_unresolvable_out_of_line_module_is_a_problem(
    tmp_path: pathlib.Path,
) -> None:
    """A declaration naming no file leaves the code it holds unknown."""
    put(tmp_path, "src/lib.rs", "#[cfg(test)]\nmod tests;\n")
    problems: list[str] = []
    assert evidence(tmp_path, problems).spawns == []
    assert any("tests" in problem for problem in problems)


def test_an_absolute_path_attribute_is_refused(tmp_path: pathlib.Path) -> None:
    """A `#[path]` value is checkout-controlled text, not a name to trust."""
    outside = tmp_path.parent / "outside_absolute.rs"
    outside.write_text(
        'fn run() {\n    Command::new("cargo").arg("build");\n}\n', encoding="utf-8"
    )
    put(
        tmp_path,
        "src/lib.rs",
        f'#[cfg(test)]\n#[path = "{outside}"]\nmod tests;\n',
    )
    problems: list[str] = []
    assert evidence(tmp_path, problems).spawns == []
    assert any("absolute path" in problem for problem in problems)


def test_a_climbing_path_attribute_is_refused(tmp_path: pathlib.Path) -> None:
    """`..` above the root would let a declaration read any file the process can."""
    outside = tmp_path.parent / "outside_climb.rs"
    outside.write_text(
        'fn run() {\n    Command::new("cargo").arg("build");\n}\n', encoding="utf-8"
    )
    put(
        tmp_path,
        "lib.rs",
        '#[cfg(test)]\n#[path = "../outside_climb.rs"]\nmod tests;\n',
    )
    problems: list[str] = []
    assert evidence(tmp_path, problems).spawns == []
    assert any("climbs out of the checkout" in problem for problem in problems)


def test_a_path_attribute_that_steps_back_inside_is_read(
    tmp_path: pathlib.Path,
) -> None:
    """Rust resolves `..` against the declaring file, so a step inside is legal.

    whitaker declares `#[path = "../tests/behaviour.rs"]` from
    `crates/x/src/driver/mod.rs`, which names `crates/x/src/tests/behaviour.rs`.
    Refusing every `..` before folding it would leave that file unread and
    report a problem about a declaration that resolves perfectly well.
    """
    put(
        tmp_path,
        "src/driver/mod.rs",
        '#[cfg(test)]\n#[path = "../tests/behaviour.rs"]\nmod behaviour;\n',
    )
    put(
        tmp_path,
        "src/tests/behaviour.rs",
        'fn run() {\n    Command::new("cargo").arg("build");\n}\n',
    )
    problems: list[str] = []
    assert [
        (fact["path"], fact["state"]) for fact in evidence(tmp_path, problems).spawns
    ] == [("src/tests/behaviour.rs", "online")]
    assert problems == []


def test_a_path_attribute_that_symlinks_outside_is_refused(
    tmp_path: pathlib.Path,
) -> None:
    """A symlink would otherwise carry an outside file's spawns into the report."""
    outside = tmp_path.parent / "outside_linked.rs"
    outside.write_text(
        'fn run() {\n    Command::new("cargo").arg("build");\n}\n', encoding="utf-8"
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "linked.rs").symlink_to(outside)
    put(
        tmp_path,
        "src/lib.rs",
        '#[cfg(test)]\n#[path = "linked.rs"]\nmod tests;\n',
    )
    problems: list[str] = []
    assert evidence(tmp_path, problems).spawns == []
    assert any("outside the checkout" in problem for problem in problems)


def test_a_path_attribute_inside_the_checkout_is_read(tmp_path: pathlib.Path) -> None:
    """Containment refuses outside paths without refusing legitimate ones."""
    put(
        tmp_path,
        "src/lib.rs",
        '#[cfg(test)]\n#[path = "elsewhere/tests.rs"]\nmod tests;\n',
    )
    put(
        tmp_path,
        "src/elsewhere/tests.rs",
        'fn run() {\n    Command::new("cargo").arg("build");\n}\n',
    )
    problems: list[str] = []
    assert [fact["state"] for fact in evidence(tmp_path, problems).spawns] == ["online"]
    assert problems == []


def test_a_test_support_crate_is_test_scope(tmp_path: pathlib.Path) -> None:
    """Test-support crates exist only to serve tests."""
    put(
        tmp_path,
        "crates/test_support/src/lib.rs",
        'pub fn run() {\n    Command::new("cargo").arg("build");\n}\n',
    )
    assert states(tmp_path) == [("command_new", "online")]


def test_a_hyphenated_test_support_crate_is_test_scope(tmp_path: pathlib.Path) -> None:
    """Cargo lets a crate directory use the hyphenated spelling."""
    put(
        tmp_path,
        "crates/test-support/src/lib.rs",
        'pub fn run() {\n    Command::new("cargo").arg("build");\n}\n',
    )
    assert states(tmp_path) == [("command_new", "online")]


def test_a_fixture_package_is_not_host_test_code(tmp_path: pathlib.Path) -> None:
    """A fixture's own cargo calls are the nested build under audit, not a test."""
    put(tmp_path, "Cargo.toml", '[workspace]\nmembers = ["crates/*"]\n')
    put(
        tmp_path,
        "tests/fixtures/a/Cargo.toml",
        '[workspace]\n\n[package]\nname = "fixture"\nversion = "0.0.0"\n',
    )
    put(
        tmp_path,
        "tests/fixtures/a/tests/spawn.rs",
        'fn run() {\n    Command::new("cargo").arg("build");\n}\n',
    )
    assert states(tmp_path) == []


def test_a_fixture_directory_outside_a_standalone_package_is_read(
    tmp_path: pathlib.Path,
) -> None:
    """A `tests/` directory with no manifest of its own is ordinary test code."""
    put(tmp_path, "Cargo.toml", '[workspace]\nmembers = ["crates/*"]\n')
    put(tmp_path, "crates/member/Cargo.toml", '[package]\nname = "member"\n')
    put(
        tmp_path,
        "crates/member/tests/fixtures/spawn.rs",
        'fn run() {\n    Command::new("cargo").arg("build");\n}\n',
    )
    assert states(tmp_path) == [("command_new", "online")]


def test_an_unbalanced_scope_is_a_problem(tmp_path: pathlib.Path) -> None:
    """A module whose body never closes leaves every span after it unknown."""
    put(
        tmp_path,
        "tests/broken.rs",
        "#[cfg(test)]\nmod tests {\n    fn run() {\n"
        '        Command::new("cargo").arg("build");\n',
    )
    problems: list[str] = []
    assert evidence(tmp_path, problems).spawns == []
    assert any("tests/broken.rs" in problem for problem in problems)


def test_an_unterminated_literal_is_a_problem(tmp_path: pathlib.Path) -> None:
    """Once a literal runs to the end of the file, the structure is a fiction."""
    put(
        tmp_path,
        "tests/broken.rs",
        'fn run() {\n    Command::new("cargo).arg("build");\n}\n',
    )
    problems: list[str] = []
    evidence(tmp_path, problems)
    assert any("tests/broken.rs" in problem for problem in problems)


def test_an_undecodable_source_is_a_problem(tmp_path: pathlib.Path) -> None:
    """A file that is not UTF-8 cannot be read at all."""
    path = tmp_path / "tests/broken.rs"
    path.parent.mkdir(parents=True)
    path.write_bytes(b'fn run() { "\xff" }\n')
    problems: list[str] = []
    evidence(tmp_path, problems)
    assert any("tests/broken.rs" in problem for problem in problems)


def test_a_trybuild_reference_in_test_scope_is_recorded(tmp_path: pathlib.Path) -> None:
    """`trybuild::TestCases` is the applicability evidence NC-008 needs."""
    put(
        tmp_path,
        "tests/ui.rs",
        "fn main() {\n    let cases = trybuild::TestCases::new();\n}\n",
    )
    facts = evidence(tmp_path).trybuild
    assert [(fact["path"], fact["line"]) for fact in facts] == [("tests/ui.rs", 2)]


def test_a_trybuild_dependency_is_recorded_from_a_manifest(
    tmp_path: pathlib.Path,
) -> None:
    """A dev-dependency on trybuild is the other half of the evidence."""
    put(tmp_path, "Cargo.toml", '[workspace]\nmembers = ["crates/*"]\n')
    put(
        tmp_path,
        "crates/member/Cargo.toml",
        '[package]\nname = "member"\n\n[dev-dependencies]\ntrybuild = "1"\n',
    )
    manifest: Manifest = {
        "package": {"name": "member"},
        "dev-dependencies": {"trybuild": "1"},
    }
    manifests = {tmp_path / "crates/member/Cargo.toml": manifest}
    facts = collect_trybuild_manifests(tmp_path, manifests, frozenset())
    assert [(fact["path"], fact["form"]) for fact in facts] == [
        ("crates/member/Cargo.toml", "manifest")
    ]


def test_a_fixture_manifest_is_not_trybuild_evidence(tmp_path: pathlib.Path) -> None:
    """A fixture's dev-dependency describes the fixture, not the host's tests."""
    manifest: Manifest = {
        "package": {"name": "fixture"},
        "dev-dependencies": {"trybuild": "1"},
    }
    manifests = {tmp_path / "tests/fixtures/a/Cargo.toml": manifest}
    assert (
        collect_trybuild_manifests(tmp_path, manifests, frozenset({"tests/fixtures/a"}))
        == []
    )


def test_fixture_directories_need_both_selectors(tmp_path: pathlib.Path) -> None:
    """A `tests/` path is not a fixture package unless Cargo can build it alone."""
    put(tmp_path, "Cargo.toml", '[workspace]\nmembers = ["crates/*"]\n')
    put(
        tmp_path,
        "tests/fixtures/a/Cargo.toml",
        '[workspace]\n\n[package]\nname = "fixture"\nversion = "0.0.0"\n',
    )
    put(tmp_path, "crates/member/Cargo.toml", '[package]\nname = "member"\n')
    from concordat.rules.nested_cargo import load_manifests

    manifests = load_manifests(tmp_path, [])
    assert fixture_directories(tmp_path, manifests) == frozenset({"tests/fixtures/a"})
