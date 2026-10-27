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
    # Words after `--` are the arguments of the program cargo runs, not of
    # cargo. Reading them as cargo's own flags would call a spawn offline that
    # can still reach the network.
    "offline-after-double-dash": Case(
        "fn run() {\n"
        '    Command::new("cargo").args(["run", "--", "--offline", "--locked"]);\n'
        "}\n",
        (("command_new", "online"),),
    ),
    "frozen-after-double-dash": Case(
        'fn run() {\n    Command::new("cargo").args(["run", "--", "--frozen"]);\n}\n',
        (("command_new", "online"),),
    ),
    # `let mut cmd = std::process::Command::new("cargo")` binds the same value
    # as the unqualified form, so a later `cmd.arg("--frozen")` is that same
    # command's flag. Reading only the unqualified spelling leaves the binding
    # uncollected and calls an offline spawn online.
    "path-qualified-binding": Case(
        "fn run() {\n"
        '    let mut cmd = std::process::Command::new("cargo");\n'
        '    cmd.arg("--frozen");\n'
        "    cmd\n"
        "}\n",
        (("command_new", "offline"),),
    ),
    # A comma inside an environment value is data, not a separator. Splitting
    # on it makes one name-value pair look like three slots, which the reader
    # cannot parse as pairs — so a command it could have settled is reported
    # unresolved and its real NC-007 finding is hidden.
    "comma-inside-an-env-value": Case(
        "fn run() {\n"
        '    Command::new("cargo")\n'
        '        .env("RUST_LOG", "info,foo=debug")\n'
        '        .arg("build");\n'
        "}\n",
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
    # Cargo treats a literal `CARGO_NET_OFFLINE` value it can read as evidence,
    # whichever way it reads it. `1` switches offline mode on exactly as `true`
    # does (measured against cargo 1.99.1), and `false`/`0`/`no`/`off` are the
    # command saying it may use the network — visible facts, not unreadable
    # ones. Reporting `unresolved` for them both hides a real NC-007 and
    # mislabels the finding as "arguments the reader cannot see".
    "env-offline-numeric-one": Case(
        "fn run() {\n"
        '    Command::new("cargo")\n'
        '        .env("CARGO_NET_OFFLINE", "1")\n'
        '        .arg("build");\n'
        "}\n",
        (("command_new", "offline"),),
    ),
    "env-offline-literal-false": Case(
        "fn run() {\n"
        '    Command::new("cargo")\n'
        '        .env("CARGO_NET_OFFLINE", "false")\n'
        '        .arg("build");\n'
        "}\n",
        (("command_new", "online"),),
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
    # rustfmt adds a trailing comma when it breaks a call over several lines,
    # so `.env("CARGO_TARGET_DIR", target,)` is ordinary formatted code. The
    # comma leaves an empty third slot; reading that as a third argument makes
    # a readable pair look unreadable, which both marks the command `unresolved`
    # and loses the offline evidence a multi-line `.env` carries.
    "multiline-env-with-trailing-comma": Case(
        "fn run() {\n"
        '    Command::new("cargo")\n'
        "        .env(\n"
        '            "CARGO_TARGET_DIR",\n'
        "            target,\n"
        "        )\n"
        '        .arg("build");\n'
        "}\n",
        (("command_new", "online"),),
    ),
    "multiline-offline-env-with-trailing-comma": Case(
        "fn run() {\n"
        '    Command::new("cargo")\n'
        "        .env(\n"
        '            "CARGO_NET_OFFLINE",\n'
        '            "true",\n'
        "        )\n"
        '        .arg("build");\n'
        "}\n",
        (("command_new", "offline"),),
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
    # `cargo build --help` and `cargo build -h` print cargo's own help, read no
    # manifest and exit 0. The rule documents them as probes, so the reader
    # must classify them as one rather than reporting a build that never runs.
    "help-flag": Case(
        'fn run() {\n    Command::new("cargo").args(["build", "--help"]);\n}\n',
        (),
    ),
    "short-help-flag": Case(
        'fn run() {\n    Command::new("cargo").args(["build", "-h"]);\n}\n',
        (),
    ),
    "global-short-help": Case(
        'fn run() {\n    Command::new("cargo").arg("-h");\n}\n',
        (),
    ),
    # theoremc probes for a cargo plugin with `cargo kani --version`. cargo
    # dispatches to the plugin, which prints its version without touching a
    # manifest, a lock or the network, so the probe cannot resolve anything.
    "plugin-version": Case(
        'fn run() {\n    Command::new("cargo").args(["kani", "--version"]);\n}\n',
        (),
    ),
    "plugin-short-version": Case(
        'fn run() {\n    Command::new("cargo").args(["nextest", "-V"]);\n}\n',
        (),
    ),
    # `cargo run help` hands `help` to the binary under test, and
    # `cargo test -- --version` hands the flag to the test binary. Both really
    # build, and neither is a cargo help or version probe.
    "help-after-run": Case(
        'fn run() {\n    Command::new("cargo").args(["run", "help"]);\n}\n',
        (("command_new", "online"),),
    ),
    "version-after-separator": Case(
        'fn run() {\n    Command::new("cargo").args(["test", "--", "--version"]);\n}\n',
        (("command_new", "online"),),
    ),
    # `cargo install --version 1.2.3` reads `--version` as the crate version to
    # install and reaches the registry. Excluding it would hide the spawn.
    "version-with-value": Case(
        'fn run() {\n    Command::new("cargo")\n'
        '        .args(["install", "--version", "1.2.3"]);\n}\n',
        (("command_new", "online"),),
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
    # A second `let` of the same name is a different command. Carrying its
    # flags onto the first one would report a non-cargo build as offline.
    "shadowed-binding": Case(
        "fn run() {\n"
        '    let mut command = Command::new("cargo");\n'
        '    command.arg("build");\n'
        '    let mut command = Command::new("echo");\n'
        '    command.arg("--frozen");\n'
        "}\n",
        (("command_new", "online"),),
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
    # An unreadable argument before `--frozen` may be `--`, which makes every
    # later word a positional argument of the program cargo runs. The flags
    # after it are then not cargo's own, so the spawn is not proven offline.
    "non-literal-argument-before-frozen": Case(
        "fn run(extra: &[&str]) {\n"
        '    Command::new("cargo").args(extra).arg("--frozen").arg("build");\n'
        "}\n",
        (("command_new", "unresolved"),),
    ),
    # The same argument *after* the offline flags cannot change them: cargo
    # reads `--frozen` before it reaches anything the reader cannot see.
    "non-literal-argument-after-frozen": Case(
        "fn run(extra: &[&str]) {\n"
        '    Command::new("cargo").arg("--frozen").args(extra).arg("build");\n'
        "}\n",
        (("command_new", "offline"),),
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


def test_a_module_declared_inside_a_test_module_is_test_scope(
    tmp_path: pathlib.Path,
) -> None:
    """An inline test module's own `mod name;` names test code.

    `#[cfg(test)] mod checks { mod helpers; }` makes the compiler build
    `src/checks/helpers.rs` into the test target even though the declaration
    carries no attribute of its own. Following only the attributed form left
    the file without test scope, so a cargo spawn there went unreported.
    """
    put(tmp_path, "src/lib.rs", "#[cfg(test)]\nmod checks {\n    mod helpers;\n}\n")
    put(
        tmp_path,
        "src/checks/helpers.rs",
        'fn run() {\n    Command::new("cargo").arg("build");\n}\n',
    )
    facts = evidence(tmp_path).spawns
    assert [(fact["path"], fact["state"]) for fact in facts] == [
        ("src/checks/helpers.rs", "online")
    ]


def test_a_nested_declaration_reads_its_own_path_attribute(
    tmp_path: pathlib.Path,
) -> None:
    """A `mod name;` inside a test module may carry its own `#[path]`.

    Rust resolves the value against the module's directory, exactly as it does
    for an attributed declaration at the same depth.
    """
    put(
        tmp_path,
        "src/lib.rs",
        '#[cfg(test)]\nmod checks {\n    #[path = "helper_file.rs"]\n'
        "    mod helpers;\n}\n",
    )
    put(
        tmp_path,
        "src/checks/helper_file.rs",
        'fn run() {\n    Command::new("cargo").arg("build");\n}\n',
    )
    assert [(fact["path"], fact["state"]) for fact in evidence(tmp_path).spawns] == [
        ("src/checks/helper_file.rs", "online")
    ]


def test_a_module_declared_outside_test_scope_is_not_followed(
    tmp_path: pathlib.Path,
) -> None:
    """Ordinary library code that declares a module is not test scope.

    Following every `mod name;` rather than only those already inside a test
    item would make `src/build/helpers.rs` test code and report a spawn there
    that no test runs.
    """
    put(tmp_path, "src/lib.rs", "mod build {\n    mod helpers;\n}\n")
    put(
        tmp_path,
        "src/build/helpers.rs",
        'fn run() {\n    Command::new("cargo").arg("build");\n}\n',
    )
    assert evidence(tmp_path).spawns == []


def test_a_config_gated_nested_declaration_is_not_test_scope(
    tmp_path: pathlib.Path,
) -> None:
    """`cfg(not(test))` inside a test module is the code tests leave out.

    The enclosing module is test scope, but this declaration asks the compiler
    to drop it from a test build, so its file is not test code and a spawn
    there must not be reported.
    """
    put(
        tmp_path,
        "src/lib.rs",
        "#[cfg(test)]\nmod checks {\n    #[cfg(not(test))]\n    mod helpers;\n}\n",
    )
    put(
        tmp_path,
        "src/checks/helpers.rs",
        'fn run() {\n    Command::new("cargo").arg("build");\n}\n',
    )
    assert evidence(tmp_path).spawns == []


def test_a_module_reached_through_a_test_module_is_test_scope(
    tmp_path: pathlib.Path,
) -> None:
    """Test scope follows a declaration out of one file and into the next.

    `#[cfg(test)] mod checks;` in `src/lib.rs` puts `src/checks.rs` in test
    scope; a `mod helpers;` in that file then reaches `src/checks/helpers.rs`,
    which the compiler builds into the test target too. Reading only the first
    hop left the second file out of scope, so a cargo spawn there produced no
    NC-007. The estate has 154 such chains.
    """
    put(tmp_path, "src/lib.rs", "#[cfg(test)]\nmod checks;\n")
    put(tmp_path, "src/checks.rs", "mod helpers;\n")
    put(
        tmp_path,
        "src/checks/helpers.rs",
        'fn run() {\n    Command::new("cargo").arg("build");\n}\n',
    )
    facts = evidence(tmp_path).spawns
    assert [(fact["path"], fact["state"]) for fact in facts] == [
        ("src/checks/helpers.rs", "online")
    ]


def test_a_path_attribute_reaches_out_of_a_whole_file_test_module(
    tmp_path: pathlib.Path,
) -> None:
    """`#[path = "../support.rs"] mod support;` names a file outside `tests/`.

    The value is resolved against the declaring file's directory, so an
    integration test can pull a support module in from beside `tests/`. The
    declaring file is a whole-file test module, which the reader never scanned
    for declarations, so the file it names stayed out of scope.
    """
    put(tmp_path, "tests/it.rs", '#[path = "../src/support.rs"]\nmod support;\n')
    put(
        tmp_path,
        "src/support.rs",
        'fn run() {\n    Command::new("cargo").arg("build");\n}\n',
    )
    facts = evidence(tmp_path).spawns
    assert [(fact["path"], fact["state"]) for fact in facts] == [
        ("src/support.rs", "online")
    ]


def test_a_whole_file_test_module_resolves_its_modules_as_a_crate_root(
    tmp_path: pathlib.Path,
) -> None:
    """`tests/it.rs` is a crate root, so `mod helpers;` names `tests/helpers.rs`.

    The compiler resolves a crate root's declarations against the directory it
    sits in and rejects `tests/it/helpers.rs`. Once a `tests/` file is scanned
    for declarations, reading its stem as a directory would report
    "names no file" against every such declaration and turn a clean repository
    into an EN-001.
    """
    put(tmp_path, "tests/it.rs", "mod helpers;\n")
    put(tmp_path, "tests/helpers.rs", "pub const VALUE: u8 = 1;\n")
    problems: list[str] = []
    evidence(tmp_path, problems)
    assert problems == []


def test_a_module_of_a_module_is_read_after_a_late_promotion(
    tmp_path: pathlib.Path,
) -> None:
    """A file read before another declaration promotes it must be read again.

    Files are scanned in reverse path order, so `src/zebra.rs` is read before
    `src/lib.rs` promotes it to whole-file test scope. The promotion alone
    never revisits `src/zebra.rs`, so its `mod helpers;` — invisible while the
    file was read as ordinary library code — stayed unresolved and
    `src/zebra/helpers.rs` drew no finding.
    """
    put(tmp_path, "src/lib.rs", "#[cfg(test)]\nmod zebra;\n")
    put(tmp_path, "src/zebra.rs", "mod helpers;\n")
    put(
        tmp_path,
        "src/zebra/helpers.rs",
        'fn run() {\n    Command::new("cargo").arg("build");\n}\n',
    )
    facts = evidence(tmp_path).spawns
    assert [(fact["path"], fact["state"]) for fact in facts] == [
        ("src/zebra/helpers.rs", "online")
    ]


def test_a_path_reached_example_is_a_crate_root(tmp_path: pathlib.Path) -> None:
    """A file `#[path]` pulls in from `examples/` is a crate root as well.

    `tests/it.rs` can reach `examples/probe.rs`, and the compiler then resolves
    that file's own `mod helpers;` against `examples/`. Reading it as a
    non-root would look in `examples/probe/helpers.rs` and find nothing.
    """
    put(
        tmp_path,
        "tests/it.rs",
        '#[path = "../examples/probe.rs"]\nmod probe;\n',
    )
    put(tmp_path, "examples/probe.rs", "mod helpers;\n")
    put(
        tmp_path,
        "examples/helpers.rs",
        'fn run() {\n    Command::new("cargo").arg("build");\n}\n',
    )
    facts = evidence(tmp_path).spawns
    assert [(fact["path"], fact["state"]) for fact in facts] == [
        ("examples/helpers.rs", "online")
    ]


def test_a_path_reached_file_resolves_its_children_beside_it(
    tmp_path: pathlib.Path,
) -> None:
    """A `#[path]`-reached file keeps its children beside it, not under its stem.

    The compiler rejects `tests/support/probe/helpers.rs` here with E0583 and
    asks for `tests/support/helpers.rs`. Offering the stem directory as well
    is harmless — only a file that exists is read — but offering *only* the
    stem directory would both miss this file and, once the declaration is
    scanned, report it as naming nothing.
    """
    put(tmp_path, "tests/it.rs", '#[path = "support/probe.rs"]\nmod probe;\n')
    put(tmp_path, "tests/support/probe.rs", "mod helpers;\n")
    put(
        tmp_path,
        "tests/support/helpers.rs",
        'fn run() {\n    Command::new("cargo").arg("build");\n}\n',
    )
    problems: list[str] = []
    facts = evidence(tmp_path, problems).spawns
    assert [(fact["path"], fact["state"]) for fact in facts] == [
        ("tests/support/helpers.rs", "online")
    ]
    assert problems == []


def test_a_file_reached_both_ways_keeps_every_base(tmp_path: pathlib.Path) -> None:
    """Both readings of one file are compiled, so both bases must survive.

    `src/checks.rs` is named here by a plain `mod checks;`, whose children live
    in `src/checks/`, and by `#[path = "checks.rs"]` from the same file, whose
    children live beside it. Each declaration is real, so the compiler builds
    whichever child exists. Recording only the last reach seen — or replacing
    the flags instead of unioning them — offers one base and reports the child
    the other base holds as naming no file.
    """
    put(
        tmp_path,
        "src/lib.rs",
        '#[cfg(test)]\nmod checks;\n#[cfg(test)]\n#[path = "checks.rs"]\nmod alias;\n',
    )
    put(tmp_path, "src/checks.rs", "mod inner;\n")
    put(
        tmp_path,
        "src/checks/inner.rs",
        'fn run() {\n    Command::new("cargo").arg("build");\n}\n',
    )
    problems: list[str] = []
    facts = evidence(tmp_path, problems).spawns
    assert [(fact["path"], fact["state"]) for fact in facts] == [
        ("src/checks/inner.rs", "online")
    ]
    assert problems == []


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


def test_a_renamed_trybuild_dependency_is_recorded_from_a_manifest(
    tmp_path: pathlib.Path,
) -> None:
    """A dependency may rename the package it pulls in.

    `tb = { package = "trybuild", version = "1" }` makes the crate available
    as `tb`, so the manifest declares trybuild while no dependency key says
    so. Reading only the keys dropped the fact, and with no direct spawn
    NC-008 then reported nothing for an entry point that does start a nested
    build.
    """
    manifest: Manifest = {
        "package": {"name": "member"},
        "dev-dependencies": {"tb": {"package": "trybuild", "version": "1"}},
    }
    manifests = {tmp_path / "member/Cargo.toml": manifest}
    facts = collect_trybuild_manifests(tmp_path, manifests, frozenset())
    assert [(fact["path"], fact["form"]) for fact in facts] == [
        ("member/Cargo.toml", "manifest")
    ]


def test_a_dependency_on_another_package_is_not_trybuild_evidence(
    tmp_path: pathlib.Path,
) -> None:
    """A rename is evidence only when it names trybuild.

    `tb = { package = "tempfile", version = "3" }` puts a crate named `tb` in
    scope without any trybuild dependency, so reporting it would claim a
    nested build the checkout does not have.
    """
    manifest: Manifest = {
        "package": {"name": "member"},
        "dev-dependencies": {"tb": {"package": "tempfile", "version": "3"}},
    }
    manifests = {tmp_path / "member/Cargo.toml": manifest}
    assert collect_trybuild_manifests(tmp_path, manifests, frozenset()) == []


def test_a_renamed_trybuild_reference_is_recorded_from_source(
    tmp_path: pathlib.Path,
) -> None:
    """Tests reach a renamed trybuild by its local name.

    The call is `tb::TestCases`, not `trybuild::TestCases`, so matching the
    package name alone still missed it. The alias has to be resolved back to
    the package it names before the source reference can be recognised.
    """
    put(tmp_path, "Cargo.toml", '[workspace]\nmembers = ["member"]\n')
    put(
        tmp_path,
        "member/Cargo.toml",
        '[package]\nname = "member"\n\n'
        '[dev-dependencies]\ntb = { package = "trybuild", version = "1" }\n',
    )
    put(
        tmp_path,
        "member/tests/ui.rs",
        "fn main() {\n    let cases = tb::TestCases::new();\n}\n",
    )
    facts = evidence(tmp_path).trybuild
    assert [(fact["path"], fact["line"], fact["form"]) for fact in facts] == [
        ("member/Cargo.toml", 1, "manifest"),
        ("member/tests/ui.rs", 2, "source"),
    ]


def test_an_alias_does_not_reach_a_sibling_package(
    tmp_path: pathlib.Path,
) -> None:
    """A rename belongs to the package that declared it.

    Only `member` renames trybuild to `tb`; `other` does not depend on it at
    all, so `tb::TestCases` in `other`'s tests names something else. Reading
    every alias against every file would report a nested build that package
    does not have, and the survey's repository counts would move for a reason
    the repository cannot fix.
    """
    put(tmp_path, "Cargo.toml", '[workspace]\nmembers = ["member", "other"]\n')
    put(
        tmp_path,
        "member/Cargo.toml",
        '[package]\nname = "member"\n\n'
        '[dev-dependencies]\ntb = { package = "trybuild", version = "1" }\n',
    )
    put(tmp_path, "other/Cargo.toml", '[package]\nname = "other"\n')
    put(
        tmp_path,
        "other/tests/ui.rs",
        "fn main() {\n    let cases = tb::TestCases::new();\n}\n",
    )

    facts = evidence(tmp_path).trybuild

    assert [(fact["path"], fact["form"]) for fact in facts] == [
        ("member/Cargo.toml", "manifest")
    ]


def test_a_hyphenated_trybuild_alias_is_matched_by_its_rust_name(
    tmp_path: pathlib.Path,
) -> None:
    """Cargo renames a dependency key's hyphens to underscores in Rust.

    `my-trybuild = { package = "trybuild" }` puts the crate in scope as
    `my_trybuild`, because a hyphen cannot appear in a Rust path. Matching the
    key as written builds a pattern for a name no source file can use, so a
    test that does reach trybuild through that rename would carry no source
    fact — and the source fact is what says the checkout actually uses
    trybuild, rather than only declaring it.
    """
    put(tmp_path, "Cargo.toml", '[workspace]\nmembers = ["member"]\n')
    put(
        tmp_path,
        "member/Cargo.toml",
        '[package]\nname = "member"\n\n'
        '[dev-dependencies]\nmy-trybuild = { package = "trybuild", version = "1" }\n',
    )
    put(
        tmp_path,
        "member/tests/ui.rs",
        "fn main() {\n    let cases = my_trybuild::TestCases::new();\n}\n",
    )

    facts = evidence(tmp_path).trybuild

    assert [(fact["path"], fact["line"], fact["form"]) for fact in facts] == [
        ("member/Cargo.toml", 1, "manifest"),
        ("member/tests/ui.rs", 2, "source"),
    ]


def test_a_plain_reference_is_recorded_under_a_virtual_workspace_manifest(
    tmp_path: pathlib.Path,
) -> None:
    """A virtual workspace root declares no dependencies of its own.

    Its `Cargo.toml` is the nearest manifest above `crates/member` until that
    package's own file is consulted, so requiring the governing manifest to
    name trybuild would drop a plain `trybuild::TestCases` reference. The
    package name names the crate whatever the declaring manifest says.
    """
    put(tmp_path, "Cargo.toml", '[workspace]\nmembers = ["crates/*"]\n')
    put(tmp_path, "crates/member/Cargo.toml", '[package]\nname = "member"\n')
    put(
        tmp_path,
        "crates/member/tests/ui.rs",
        "fn main() {\n    let cases = trybuild::TestCases::new();\n}\n",
    )

    facts = evidence(tmp_path).trybuild

    assert [(fact["path"], fact["form"]) for fact in facts] == [
        ("crates/member/tests/ui.rs", "source")
    ]


def test_a_reference_through_an_unrelated_alias_is_not_recorded(
    tmp_path: pathlib.Path,
) -> None:
    """A local name that does not name trybuild proves nothing.

    `rb::TestCases` could be any crate's type, so treating every `TestCases`
    path as trybuild evidence would report a nested build for code that has
    none.
    """
    put(tmp_path, "Cargo.toml", '[workspace]\nmembers = ["member"]\n')
    put(
        tmp_path,
        "member/Cargo.toml",
        '[package]\nname = "member"\n\n'
        '[dev-dependencies]\nrb = { package = "rstest-bdd", version = "0" }\n',
    )
    put(
        tmp_path,
        "member/tests/ui.rs",
        "fn main() {\n    let cases = rb::TestCases::new();\n}\n",
    )
    assert evidence(tmp_path).trybuild == []


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
