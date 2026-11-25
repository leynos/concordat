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
