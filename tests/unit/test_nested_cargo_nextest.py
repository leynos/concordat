"""Audit the nextest configuration of compiler-spawning tests through real files.

The configurations mirror the corpus: rstest-bdd and netsuke after their fixes
(a serial group, timeouts and filters that name the spawning tests), catnap's
slow-timeout-only file, and the conservative cases that must stay quiet
(`cargo metadata`, Python tests, helpers).
"""

from __future__ import annotations

import dataclasses
import typing as typ

import pytest

from concordat.rules import runner
from concordat.rules.nested_cargo_nextest_envelope import (
    build_nested_cargo_nextest_envelope,
)

if typ.TYPE_CHECKING:
    import pathlib

RULE_ID = "nested-cargo-nextest"
BUILD_TEST = (
    "use std::process::Command;\n"
    "#[test]\nfn compiles() {\n"
    '    Command::new("cargo").args(["build", "--bin", "x"]).status().unwrap();\n'
    "}\n"
)
RUSTC_TEST = (
    "use std::process::Command;\n"
    "#[test]\nfn direct() {\n"
    '    Command::new("rustc").arg("--version").status().unwrap();\n}\n'
)
METADATA_TEST = (
    "use std::process::Command;\n"
    "#[test]\nfn reads() {\n"
    '    Command::new("cargo").args(["metadata"]).output().unwrap();\n}\n'
)
CLEAN = (
    "[test-groups.cargo-spawning]\nmax-threads = 1\n"
    '[profile.default]\nslow-timeout = { period = "60s", terminate-after = 5 }\n'
    'global-timeout = "30m"\n'
    '[[profile.default.overrides]]\nfilter = "binary(ui)"\n'
    'test-group = "cargo-spawning"\n'
)


def put(root: pathlib.Path, relative: str, text: str) -> None:
    """Write one file, creating its directory."""
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def ids(root: pathlib.Path) -> set[str]:
    """Return the rule ids the real policy reports for a checkout."""
    return {finding.rule_id for finding in runner.run_rule(RULE_ID, root).findings}


@dataclasses.dataclass(frozen=True, slots=True)
class Case:
    """One source, one configuration and the findings they must draw."""

    source: str
    config: str | None
    expected: set[str]
    path: str = "tests/ui.rs"


NO_GROUP = CLEAN.replace("max-threads = 1", "max-threads = 4")
NO_SLOW = CLEAN.replace('slow-timeout = { period = "60s", terminate-after = 5 }\n', "")
NO_GLOBAL = CLEAN.replace('global-timeout = "30m"\n', "")
NOT_NAMED = CLEAN.replace("binary(ui)", "binary(other)")
CASES = {
    "bounded": Case(BUILD_TEST, CLEAN, set()),
    "no-configuration": Case(BUILD_TEST, None, {"NC-004"}),
    "no-serial-group": Case(BUILD_TEST, NO_GROUP, {"NC-004"}),
    "no-slow-timeout": Case(BUILD_TEST, NO_SLOW, {"NC-004"}),
    "no-global-timeout": Case(BUILD_TEST, NO_GLOBAL, {"NC-004"}),
    "binary-not-named": Case(BUILD_TEST, NOT_NAMED, {"NC-005"}),
    "test-named-instead-of-binary": Case(
        BUILD_TEST, CLEAN.replace("binary(ui)", "test(compiles)"), set()
    ),
    "direct-rustc-counts": Case(RUSTC_TEST, None, {"NC-004"}),
    "cargo-metadata-does-not": Case(METADATA_TEST, None, set()),
    "helper-judged-at-repository-level": Case(
        BUILD_TEST, CLEAN.replace("binary(ui)", "binary(other)"), set(), "src/helper.rs"
    ),
}


@pytest.mark.parametrize("case", CASES.values(), ids=CASES.keys())
def test_layouts_draw_the_expected_findings(tmp_path: pathlib.Path, case: Case) -> None:
    """The real policy reports each layout's findings and nothing else."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    source = case.source
    if case.path.startswith("src/"):
        source = "#[cfg(test)]\nmod tests {\n" + source + "}\n"
    put(tmp_path, case.path, source)
    if case.config is not None:
        put(tmp_path, ".config/nextest.toml", case.config)
    assert ids(tmp_path) == case.expected


def test_no_spawner_needs_no_configuration(tmp_path: pathlib.Path) -> None:
    """A repository whose tests start no compiler is compliant."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/plain.rs", "#[test]\nfn adds() { assert_eq!(1 + 1, 2); }\n")
    assert ids(tmp_path) == set()


def test_a_spawn_outside_test_code_is_not_counted(tmp_path: pathlib.Path) -> None:
    """A build script that runs cargo is not a test."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "build.rs", BUILD_TEST)
    put(tmp_path, "src/lib.rs", BUILD_TEST.replace("#[test]", ""))
    assert ids(tmp_path) == set()


def test_a_python_test_is_left_to_its_own_runner(tmp_path: pathlib.Path) -> None:
    """Pytest bounds a Python test; nextest configuration is not demanded for it."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/test_build.py", 'subprocess.run(["cargo", "build"])\n')
    assert ids(tmp_path) == set()


def test_an_unreadable_configuration_is_indeterminate(tmp_path: pathlib.Path) -> None:
    """A configuration that is not TOML is a fact, never a pass."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/ui.rs", BUILD_TEST)
    put(tmp_path, ".config/nextest.toml", "[profile\nbroken")
    assert runner.run_rule(RULE_ID, tmp_path).verdict == "indeterminate"


def test_the_envelope_records_what_the_configuration_provides(
    tmp_path: pathlib.Path,
) -> None:
    """The facts name the serial groups, the overrides and both timeouts."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/ui.rs", BUILD_TEST)
    put(tmp_path, ".config/nextest.toml", CLEAN)
    envelope = build_nested_cargo_nextest_envelope(tmp_path)
    assert envelope["nextest"] == {
        "state": "present",
        "error": None,
        "serial_groups": ["cargo-spawning"],
        "grouped_override": True,
        "slow_timeout": True,
        "global_timeout": True,
    }
    (spawner,) = envelope["spawners"]
    assert (spawner["path"], spawner["kind"], spawner["binary"]) == (
        "tests/ui.rs",
        "cargo",
        "ui",
    )
    assert spawner["named"] is True


def test_a_slow_timeout_in_an_override_counts(tmp_path: pathlib.Path) -> None:
    """Netsuke sets slow-timeout on overrides, not on the profile."""
    config = NO_SLOW + (
        '[[profile.default.overrides]]\nfilter = "binary(ui)"\n'
        'slow-timeout = { period = "60s", terminate-after = 15 }\n'
    )
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/ui.rs", BUILD_TEST)
    put(tmp_path, ".config/nextest.toml", config)
    assert ids(tmp_path) == set()


def test_a_global_timeout_in_another_profile_counts(tmp_path: pathlib.Path) -> None:
    """Netsuke sets global-timeout in profile.ci only."""
    config = NO_GLOBAL + '[profile.ci]\nglobal-timeout = "16m"\n'
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/ui.rs", BUILD_TEST)
    put(tmp_path, ".config/nextest.toml", config)
    assert ids(tmp_path) == set()
