"""Check each pass of a Makefile `test` target against what prepared it.

The rstest-bdd shape is the reason this reader exists: the Makefile prepared the
first test pass with its flags and left the second pass, which builds a different
feature set, to compile inside the timed run. The cases pin which preparations
count for which pass, and the shapes that look like preparation but warm nothing.
"""

from __future__ import annotations

import re
import typing as typ

import pytest

from concordat.rules import runner
from concordat.rules.nested_cargo_makefile_passes import makefile_passes

if typ.TYPE_CHECKING:
    import pathlib

RULE_ID = "nested-cargo-cold-build"
TRYBUILD_TEST = """\
#[test]
fn macros_compile() {
    let t = trybuild::TestCases::new();
    t.pass("tests/fixtures/ok.rs");
}
"""
CARGO_TEST = """\
#[test]
fn builds_a_fixture() {
    std::process::Command::new("cargo").arg("build").status().unwrap();
}
"""


def put(root: pathlib.Path, relative: str, text: str) -> None:
    """Write one file, creating its directory."""
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def checkout(
    root: pathlib.Path, makefile: str, test_source: str = TRYBUILD_TEST
) -> None:
    """Write a checkout with one nested-build test and the given Makefile.

    Every plain target is declared `.PHONY` first, as a Makefile does for the
    targets that name no file, unless the text declares its own.
    """
    targets = re.findall(r"^([A-Za-z][\w-]*):", makefile, flags=re.MULTILINE)
    declaration = f".PHONY: {' '.join(targets)}\n" if ".PHONY" not in makefile else ""
    put(root, "Cargo.toml", '[package]\nname = "app"\n')
    put(root, "tests/ui.rs", test_source)
    put(root, "Makefile", declaration + makefile)


def statuses(root: pathlib.Path) -> list[tuple[int, str]]:
    """Return each pass's line and status, in walk order.

    Lines count from the Makefile text the case wrote: the `.PHONY` declaration
    `checkout` puts first is not counted.

    Returns
    -------
    list[tuple[int, str]]
        The line and status of each pass.
    """
    return [(fact["line"] - 1, fact["status"]) for fact in makefile_passes(root)]


def findings(root: pathlib.Path) -> list[tuple[str, str, int]]:
    """Return the rule's findings on the Makefile as (rule, verdict, line)."""
    result = runner.run_rule(RULE_ID, root)
    return [
        (found.rule_id, found.verdict, found.line - 1)
        for found in result.findings
        if found.path == "Makefile"
    ]


def test_a_pass_with_its_own_filtered_run_first_is_warmed(
    tmp_path: pathlib.Path,
) -> None:
    """A real run of the pass's own flags, ahead of it, warms it."""
    checkout(
        tmp_path,
        "test:\n"
        "\tcargo nextest run --all-features -E 'test(/prepare$$/)'\n"
        "\tcargo nextest run --all-features\n",
    )
    assert statuses(tmp_path) == [(3, "warmed")]


def test_the_rstest_bdd_shape_leaves_the_second_pass_unprepared(
    tmp_path: pathlib.Path,
) -> None:
    """The first pass was prepared; the `--no-default-features` pass was not."""
    makefile = (
        "PREPARE_FLAGS ?= --all-features\n"
        "test:\n"
        "\t$(MAKE) prepare\n"
        "\tcargo nextest run --all-features\n"
        "\tcargo nextest run -p app --no-default-features\n"
        "\n"
        "prepare:\n"
        "\tcargo nextest run $(PREPARE_FLAGS) -E 'test(/prepare$$/)'\n"
    )
    checkout(tmp_path, makefile)
    assert statuses(tmp_path) == [(4, "warmed"), (5, "unprepared")]


def test_a_make_override_hands_the_second_pass_its_own_flags(
    tmp_path: pathlib.Path,
) -> None:
    """`$(MAKE) prepare PREPARE_FLAGS=..` prepares the target with that value."""
    makefile = (
        "PREPARE_FLAGS ?= --all-features\n"
        "test:\n"
        "\t$(MAKE) prepare\n"
        "\tcargo nextest run --all-features\n"
        '\t$(MAKE) prepare PREPARE_FLAGS="-p app --no-default-features"\n'
        "\tcargo nextest run -p app --no-default-features\n"
        "\n"
        "prepare:\n"
        "\tcargo nextest run $(PREPARE_FLAGS) -E 'test(/prepare$$/)'\n"
    )
    checkout(tmp_path, makefile)
    assert statuses(tmp_path) == [(4, "warmed"), (6, "warmed")]


def test_a_probe_that_picks_the_runner_prepares_when_both_branches_do(
    tmp_path: pathlib.Path,
) -> None:
    """`if command -v cargo-nextest; then A; else B; fi` is one preparation."""
    makefile = (
        "test:\n"
        "\tif command -v cargo-nextest >/dev/null 2>&1; then \\\n"
        "\t\tcargo nextest run --all-features -E 'test(/prepare$$/)'; \\\n"
        "\telse \\\n"
        "\t\tcargo test --all-features prepare; \\\n"
        "\tfi\n"
        "\tcargo nextest run --all-features\n"
    )
    checkout(tmp_path, makefile)
    assert statuses(tmp_path) == [(7, "warmed")]


def test_a_preparation_in_only_one_branch_prepares_nothing(
    tmp_path: pathlib.Path,
) -> None:
    """A branch the probe may not take is not a preparation."""
    makefile = (
        "test:\n"
        "\tif command -v cargo-nextest >/dev/null 2>&1; then \\\n"
        "\t\tcargo nextest run --all-features -E 'test(/prepare$$/)'; \\\n"
        "\tfi\n"
        "\tcargo nextest run --all-features\n"
    )
    checkout(tmp_path, makefile)
    assert statuses(tmp_path) == [(5, "unprepared")]


def test_a_compile_alone_is_compiled_not_warmed(tmp_path: pathlib.Path) -> None:
    """`--no-run` of the same flags prepares the build, not trybuild's own tree."""
    checkout(
        tmp_path,
        "test:\n\tcargo test --no-run --all-features\n\tcargo test --all-features\n",
    )
    assert statuses(tmp_path) == [(3, "compiled")]


def test_a_compile_alone_does_not_satisfy_a_trybuild_family(
    tmp_path: pathlib.Path,
) -> None:
    """The rule reports the pass: `--no-run` leaves trybuild cold."""
    checkout(
        tmp_path,
        "test:\n\tcargo test --no-run --all-features\n\tcargo test --all-features\n",
    )
    assert findings(tmp_path) == [("NC-010", "noncompliant", 3)]


def test_a_compile_alone_satisfies_a_plain_cargo_build(tmp_path: pathlib.Path) -> None:
    """A test that only spawns `cargo build` is warmed by the compile."""
    checkout(
        tmp_path,
        "test:\n\tcargo test --no-run --all-features\n\tcargo test --all-features\n",
        CARGO_TEST,
    )
    assert findings(tmp_path) == []


@pytest.mark.parametrize(
    "makefile",
    [
        # Features, packages, target, profile and flags must all match.
        "test:\n\tcargo test --no-run\n\tcargo test --all-features\n",
        "test:\n\tcargo test --no-run -p a\n\tcargo test -p b\n",
        "test:\n\tcargo test --no-run --release\n\tcargo test\n",
        "test:\n\tcargo test --no-run --target a\n\tcargo test --target b\n",
        "test:\n\tRUSTFLAGS=-Dwarnings cargo test --no-run\n\tcargo test\n",
        # The preparation comes after the pass.
        "test:\n\tcargo test\n\tcargo test --no-run\n",
        # `;` and `||` do not make the pass wait on the preparation.
        "test:\n\tcargo test --no-run ; cargo test\n",
        "test:\n\tcargo test --no-run || cargo test\n",
        # An ignored error line may have failed.
        "test:\n\t-cargo test --no-run\n\tcargo test\n",
        # Sibling prerequisites are not ordered.
        "test: run prepare\n\nrun:\n\tcargo test\n\nprepare:\n\tcargo test --no-run\n",
    ],
)
def test_a_preparation_that_does_not_match_or_order_prepares_nothing(
    makefile: str, tmp_path: pathlib.Path
) -> None:
    """A different build, a later one, or an unordered one leaves the pass cold."""
    checkout(tmp_path, makefile, CARGO_TEST)
    found = [status for _line, status in statuses(tmp_path)]
    assert "unprepared" in found


def test_a_prerequisite_prepares_the_recipe_that_follows_it(
    tmp_path: pathlib.Path,
) -> None:
    """Make runs a rule's prerequisites before its recipe."""
    checkout(
        tmp_path,
        "test: prepare\n\tcargo test\n\nprepare:\n\tcargo test --no-run\n",
        CARGO_TEST,
    )
    assert statuses(tmp_path) == [(2, "compiled")]


def test_a_unresolved_reference_that_is_the_same_on_both_sides_matches(
    tmp_path: pathlib.Path,
) -> None:
    """The same `$(FLAGS)` in both commands reads as the same build."""
    checkout(
        tmp_path,
        "test:\n\tcargo test --no-run $(FLAGS)\n\tcargo test $(FLAGS)\n",
        CARGO_TEST,
    )
    assert statuses(tmp_path) == [(3, "compiled")]


def test_an_unresolved_reference_that_differs_is_indeterminate(
    tmp_path: pathlib.Path,
) -> None:
    """Different unresolved flags may or may not build the same thing."""
    checkout(
        tmp_path,
        "test:\n\tcargo test --no-run $(PREPARE)\n\tcargo test $(RUN)\n",
        CARGO_TEST,
    )
    assert statuses(tmp_path) == [(3, "indeterminate")]


def test_a_test_runner_the_makefile_leaves_unresolved_is_indeterminate(
    tmp_path: pathlib.Path,
) -> None:
    """`$(RUNNER) --all-features` may be the test runner."""
    checkout(tmp_path, "test:\n\tcargo test --no-run\n\t$(RUNNER) --all-features\n")
    assert statuses(tmp_path) == [(3, "indeterminate")]


def test_a_filtered_run_is_a_preparation_and_is_not_judged(
    tmp_path: pathlib.Path,
) -> None:
    """The reader cannot know which tests a filter leaves, so it only warms."""
    checkout(tmp_path, "test:\n\tcargo test --all-features prepare\n", CARGO_TEST)
    assert statuses(tmp_path) == []


def test_a_checkout_without_a_makefile_has_no_passes(tmp_path: pathlib.Path) -> None:
    """The reader follows the Makefile's `test` target and nothing else."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "app"\n')
    assert makefile_passes(tmp_path) == []


def test_a_makefile_the_reader_does_not_follow_is_indeterminate(
    tmp_path: pathlib.Path,
) -> None:
    """`.ONESHELL` changes what a failed line does to the next."""
    checkout(tmp_path, ".ONESHELL:\ntest:\n\tcargo test --no-run\n\tcargo test\n")
    assert statuses(tmp_path) == [(2, "indeterminate")]


def test_a_repository_with_a_setup_script_is_not_judged_by_pass(
    tmp_path: pathlib.Path,
) -> None:
    """Nextest runs the setup script before every pass, so the Makefile is moot."""
    checkout(
        tmp_path, "test:\n\tcargo nextest run\n\tcargo nextest run --all-features\n"
    )
    put(
        tmp_path,
        ".config/nextest.toml",
        '[scripts.setup.prepare]\ncommand = "cargo build"\n'
        '[[profile.default.scripts]]\nfilter = "all()"\nsetup = "prepare"\n',
    )
    assert findings(tmp_path) == []


@pytest.mark.parametrize(
    "first",
    [
        "$(RUNNER) --no-run --all-features",
        "$(MAKE) -C sub test",
    ],
)
def test_an_unreadable_command_first_leaves_a_later_pass_indeterminate(
    tmp_path: pathlib.Path, first: str
) -> None:
    """A command the reader cannot follow may have built what the pass needs."""
    checkout(tmp_path, f"test:\n\t{first}\n\tcargo test --all-features\n")
    assert [status for _line, status in statuses(tmp_path)] == [
        "indeterminate",
        "indeterminate",
    ]


def test_a_lib_run_does_not_warm_the_integration_tests_nested_builds(
    tmp_path: pathlib.Path,
) -> None:
    """`--lib` runs unit tests only; a trybuild build in `tests/` stays cold."""
    checkout(
        tmp_path,
        "test:\n\tcargo test --lib --all-features\n\tcargo test --all-features\n",
    )
    assert statuses(tmp_path) == [(3, "compiled")]


def test_a_test_target_run_warms_the_nested_builds_it_holds(
    tmp_path: pathlib.Path,
) -> None:
    """`--test ui` runs the integration test that holds the trybuild build."""
    checkout(
        tmp_path,
        "test:\n\tcargo test --test ui --all-features\n\tcargo test --all-features\n",
    )
    assert statuses(tmp_path) == [(3, "warmed")]


def test_a_pass_records_the_test_targets_its_warming_runs_were_limited_to(
    tmp_path: pathlib.Path,
) -> None:
    """`--test other` warms `other` only; the policy compares it with the build."""
    checkout(
        tmp_path,
        "test:\n"
        "\tcargo test --test other --all-features\n"
        "\tcargo test --all-features\n",
    )
    [fact] = makefile_passes(tmp_path)
    assert (fact["status"], fact["runner"], fact["warmed_tests"]) == (
        "warmed",
        "cargo",
        ["other"],
    )


def test_an_unrestricted_warming_run_leaves_no_target_ruled_out(
    tmp_path: pathlib.Path,
) -> None:
    """A run limited by a name filter, not a target, may have run any test."""
    checkout(
        tmp_path,
        "test:\n\tcargo test --test other --all-features\n"
        "\tcargo test --all-features prepare\n\tcargo test --all-features\n",
    )
    [fact] = makefile_passes(tmp_path)
    assert fact["warmed_tests"] == []


def test_a_make_override_of_a_variable_that_names_a_target_is_not_followed(
    tmp_path: pathlib.Path,
) -> None:
    """An override can change which target `$(T)` defines, so the call is unread."""
    checkout(
        tmp_path,
        "T := prepare\n"
        "$(T):\n\tcargo test --no-run --all-features\n"
        "test:\n\t$(MAKE) prepare T=other\n\tcargo test --all-features\n",
    )
    assert [status for _line, status in statuses(tmp_path)] == ["indeterminate"] * 2


def test_an_override_of_a_variable_behind_a_target_name_is_not_followed(
    tmp_path: pathlib.Path,
) -> None:
    """`T` reads `SUFFIX`, so overriding `SUFFIX` can change which target `$(T)` is."""
    checkout(
        tmp_path,
        "SUFFIX := prepare\nT = $(SUFFIX)\n"
        "$(T):\n\tcargo test --no-run --all-features\n"
        "test:\n\t$(MAKE) prepare SUFFIX=other\n\tcargo test --all-features\n",
    )
    assert [status for _line, status in statuses(tmp_path)] == ["indeterminate"] * 2


def test_an_unsettled_target_name_that_may_define_test_is_indeterminate(
    tmp_path: pathlib.Path,
) -> None:
    """`T != printf test` makes `$(T)` the `test` target the reader cannot index."""
    checkout(tmp_path, "T != printf test\n$(T):\n\tcargo test --all-features\n")
    assert [status for _line, status in statuses(tmp_path)] == ["indeterminate"]


def test_a_test_target_named_before_a_late_assignment_is_indeterminate(
    tmp_path: pathlib.Path,
) -> None:
    """Make defines `test` where it reads `test$(SUFFIX)`, before `SUFFIX` is set."""
    checkout(tmp_path, "test$(SUFFIX):\n\tcargo test --all-features\nSUFFIX := other\n")
    assert [status for _line, status in statuses(tmp_path)] == ["indeterminate"]


def test_an_included_file_may_define_the_test_target(tmp_path: pathlib.Path) -> None:
    """A Makefile that only includes another has not been shown to lack `test`."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "app"\n')
    put(tmp_path, "tests/ui.rs", TRYBUILD_TEST)
    put(tmp_path, "Makefile", "include tests.mk\n")
    put(tmp_path, "tests.mk", "test:\n\tcargo test\n")
    assert [fact["status"] for fact in makefile_passes(tmp_path)] == ["indeterminate"]


@pytest.mark.parametrize(
    ("pattern", "statuses_found"),
    [("te%", ["indeterminate"]), ("%st", ["indeterminate"]), ("foo%", [])],
)
def test_a_pattern_rule_that_matches_test_may_supply_it(
    tmp_path: pathlib.Path, pattern: str, statuses_found: list[str]
) -> None:
    """`te%:` builds `make test`; a pattern that cannot match leaves no test pass."""
    checkout(tmp_path, f"{pattern}:\n\tcargo test --all-features\n")
    assert [fact["status"] for fact in makefile_passes(tmp_path)] == statuses_found


@pytest.mark.parametrize(
    ("phony", "statuses_found"),
    [(".PHONY: other\n", ["indeterminate"]), (".PHONY: test\n", [])],
)
def test_a_recipe_less_test_target_may_take_a_matching_pattern_recipe(
    tmp_path: pathlib.Path, phony: str, statuses_found: list[str]
) -> None:
    """Make searches pattern rules for a `test` with no recipe, unless `.PHONY`."""
    checkout(tmp_path, f"{phony}test:\nte%:\n\tcargo test --all-features\n")
    assert [fact["status"] for fact in makefile_passes(tmp_path)] == statuses_found


@pytest.mark.parametrize(
    ("phony", "statuses_found"),
    [
        (".PHONY: test\n", ["indeterminate"]),
        (".PHONY: test integration\n", []),
    ],
)
def test_a_recipe_less_prerequisite_may_take_a_matching_pattern_recipe(
    tmp_path: pathlib.Path, phony: str, statuses_found: list[str]
) -> None:
    """`test: integration` beside `int%:` runs the pattern recipe, unless `.PHONY`."""
    checkout(tmp_path, f"{phony}test: integration\nintegration:\nint%:\n\tcargo test\n")
    assert [fact["status"] for fact in makefile_passes(tmp_path)] == statuses_found


@pytest.mark.parametrize(
    ("phony", "statuses_found"),
    [
        (".PHONY: test\n", ["indeterminate"]),
        (".PHONY: test integration\n", []),
    ],
)
def test_a_prerequisite_with_no_rule_may_take_a_matching_pattern_recipe(
    tmp_path: pathlib.Path, phony: str, statuses_found: list[str]
) -> None:
    """`test: integration` beside `int%:` runs the pattern recipe, unless `.PHONY`."""
    checkout(tmp_path, f"{phony}test: integration\nint%:\n\tcargo test\n")
    assert [fact["status"] for fact in makefile_passes(tmp_path)] == statuses_found


@pytest.mark.parametrize(
    ("phony", "statuses_found"),
    [
        (".PHONY: test\n", ["indeterminate", "indeterminate"]),
        (".PHONY: test prepare\n", ["unprepared"]),
    ],
)
def test_an_implicit_preparation_leaves_a_later_pass_indeterminate(
    tmp_path: pathlib.Path, phony: str, statuses_found: list[str]
) -> None:
    """A pattern recipe may warm the build, so a later pass is not `unprepared`."""
    makefile = (
        f"{phony}test: prepare\n\tcargo test --all-features\n"
        "prepare:\npre%:\n\tcargo test --test ui\n"
    )
    checkout(tmp_path, makefile)
    found = [fact["status"] for fact in makefile_passes(tmp_path)]
    assert sorted(found) == sorted(statuses_found)


@pytest.mark.parametrize(
    ("phony", "first", "statuses_found"),
    [
        (".PHONY: test\n", "--no-run", ["indeterminate"]),
        (".PHONY: test prepare\n", "--no-run", ["compiled"]),
        (".PHONY: test\n", "--test other", ["indeterminate"]),
    ],
)
def test_an_implicit_preparation_leaves_compiled_and_restricted_passes_open(
    tmp_path: pathlib.Path, phony: str, first: str, statuses_found: list[str]
) -> None:
    """A pattern recipe may warm what a compiled or restricted pass finds cold."""
    makefile = (
        f"{phony}test: prepare\n\tcargo test {first} --all-features\n"
        "\tcargo test --all-features\n"
        "prepare:\npre%:\n\tcargo test --test ui --all-features\n"
    )
    checkout(tmp_path, makefile)
    found = [fact["status"] for fact in makefile_passes(tmp_path)]
    assert found[-1:] == statuses_found


def test_a_recipe_less_test_target_beside_a_non_matching_pattern_is_not_flagged(
    tmp_path: pathlib.Path,
) -> None:
    """A pattern that cannot match `test` leaves the recipe-less target alone."""
    checkout(tmp_path, ".PHONY: other\ntest:\nfoo%:\n\tcargo test --all-features\n")
    assert [fact["status"] for fact in makefile_passes(tmp_path)] == []


@pytest.mark.parametrize(
    "makefile",
    [
        ".DEFAULT:\n\tcargo test --all-features\n",
        "$(eval $(call rule,test))\n",
        "$(foreach t,test,$(eval $(t):))\n",
    ],
)
def test_a_fallback_or_generated_rule_may_supply_test(
    tmp_path: pathlib.Path, makefile: str
) -> None:
    """`.DEFAULT` builds any target and `$(eval ...)` writes rules unseen."""
    checkout(tmp_path, makefile)
    assert [fact["status"] for fact in makefile_passes(tmp_path)] == ["indeterminate"]


def test_a_help_request_prepares_nothing(tmp_path: pathlib.Path) -> None:
    """`cargo build --help` compiles nothing, so the pass is still unprepared."""
    checkout(tmp_path, "test:\n\tcargo build --help\n\tcargo test --all-features\n")
    assert statuses(tmp_path) == [(3, "unprepared")]


def test_a_preparation_skipped_by_a_short_circuit_in_a_branch_is_not_certain(
    tmp_path: pathlib.Path,
) -> None:
    """`false && cargo ...` in a branch never builds, yet the branch succeeds."""
    makefile = (
        "test:\n"
        "\tif command -v cargo-nextest >/dev/null 2>&1; then \\\n"
        "\t\tfalse && cargo test --no-run --all-features; true; \\\n"
        "\telse \\\n"
        "\t\tcargo test --no-run --all-features; \\\n"
        "\tfi\n"
        "\tcargo test --all-features\n"
    )
    checkout(tmp_path, makefile)
    assert statuses(tmp_path) == [(7, "unprepared")]
