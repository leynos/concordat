"""Read a Makefile `test` target that prepares the nested builds before it tests.

A repository can declare where its nested compile work is paid for in the
Makefile instead of a nextest setup script (combobulate's `prepare-ui-gate`,
whitaker's preparation target). The cases pin which shapes count: a target that
compiles, reached before the test command through a prerequisite or a
success-gated command, and the shapes that look similar but prove nothing.
"""

from __future__ import annotations

import re
import typing as typ

import pytest

from concordat.rules import runner
from concordat.rules.nested_cargo_makefile_preparation import makefile_prepares

if typ.TYPE_CHECKING:
    import pathlib

RULE_ID = "nested-cargo-cold-build"

TRYBUILD_TEST = """\
#[test]
fn step_macros_compile() {
    let t = trybuild::TestCases::new();
    t.pass("tests/fixtures/ok.rs");
}
"""


def put(root: pathlib.Path, relative: str, text: str) -> None:
    """Write one file, creating its directory."""
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def reported(root: pathlib.Path) -> bool:
    """Return whether the rule reports the trybuild test as unprepared."""
    result = runner.run_rule(RULE_ID, root)
    return any(
        (found.rule_id, found.path) == ("NC-010", "tests/ui.rs")
        for found in result.findings
    )


def checkout(root: pathlib.Path, makefile: str, *, is_phony: bool = True) -> None:
    """Write a checkout with one trybuild test and the given Makefile.

    Every target is declared `.PHONY` first, as a Makefile does for targets that
    name no file, unless *is_phony* is false or the text declares its own.
    """
    targets = re.findall(r"^([A-Za-z][\w-]*):", makefile, flags=re.MULTILINE)
    declares = is_phony and ".PHONY" not in makefile
    declaration = f".PHONY: {' '.join(targets)}\n" if declares else ""
    put(root, "Cargo.toml", '[package]\nname = "app"\n')
    put(root, "tests/ui.rs", TRYBUILD_TEST)
    put(root, "Makefile", declaration + makefile)


def test_a_checkout_with_no_makefile_preparation_is_reported(
    tmp_path: pathlib.Path,
) -> None:
    """The baseline: a trybuild test and no declared preparation."""
    checkout(tmp_path, "test:\n\tcargo nextest run\n")
    assert reported(tmp_path)


@pytest.mark.parametrize(
    "makefile",
    [
        # A prerequisite that builds.
        "test: prepare\n\tcargo nextest run\n\nprepare:\n\tcargo build --all-targets\n",
        # A prerequisite that builds through another target.
        (
            "test: prepare\n\tcargo nextest run\n\n"
            "prepare: warm\n\nwarm:\n\tcargo test --no-run\n"
        ),
        # An earlier recipe line that builds.
        "test:\n\tcargo build --tests\n\tcargo nextest run\n",
        # An earlier command on the same line, joined by `&&`.
        "test:\n\tcargo build --tests && cargo nextest run\n",
        # A recursive make of a preparing target, on its own line or gated.
        (
            "test:\n\t$(MAKE) prepare\n\tcargo nextest run\n\n"
            "prepare:\n\tcargo bench --no-run\n"
        ),
        "test:\n\tmake prepare && cargo test\n\nprepare:\n\tcargo nextest archive\n",
        # A plain variable names the cargo, and `$(shell ...)` elsewhere is opaque.
        (
            "CARGO ?= cargo\nFLAGS := $(shell uname -s)\n\n"
            "test: prepare\n\t$(CARGO) nextest run\n\nprepare:\n\t$(CARGO) build\n"
        ),
        # Unresolved flags do not stop a build being a build.
        "test: prepare\n\tcargo nextest run\n\nprepare:\n\tcargo build $(FLAGS)\n",
        # A compile-only command ahead of the test is the preparation, not the
        # test boundary, on a recipe line and on a joined line.
        "test:\n\tcargo test --no-run\n\tcargo nextest run\n",
        "test:\n\tcargo nextest run --no-run && cargo nextest run\n",
        # A target that builds and then tests still prepares for its own tests.
        "test: prepare\n\nprepare:\n\tcargo build\n\tcargo nextest run\n",
        # A prerequisite that tests is prepared when its own prerequisite builds.
        (
            "test: run-tests\n\nrun-tests: prepare\n\tcargo nextest run\n\n"
            "prepare:\n\tcargo build\n"
        ),
        # Sibling prerequisites that only build are all preparation.
        (
            "test: warm tools\n\tcargo nextest run\n\n"
            "warm:\n\tcargo build\n\ntools:\n\tcargo fmt\n"
        ),
        # A target name that settles to a plain word is filed under that word,
        # so `$(T)` is the `prepare` rule the test waits on.
        (
            "T := prepare\n.PHONY: prepare\n$(T):\n\tcargo build\n"
            "test: prepare\n\tcargo nextest run\n"
        ),
        # A target name built from a plain variable settles to an ordinary
        # pattern rule, which cannot be a special target.
        (
            "APP := tool\n\ntarget/%/$(APP):\n\tcargo build --release\n\n"
            "test: prepare\n\tcargo nextest run\n\nprepare:\n\tcargo build\n"
        ),
    ],
)
def test_a_target_that_compiles_before_the_tests_prepares_the_builds(
    makefile: str, tmp_path: pathlib.Path
) -> None:
    """A success-gated compile ahead of the test command declares the preparation."""
    checkout(tmp_path, makefile)
    assert not reported(tmp_path)


@pytest.mark.parametrize(
    "makefile",
    [
        # The build runs after the test, so it prepares nothing for it.
        "test:\n\tcargo nextest run\n\tcargo build\n",
        # `;` and `||` do not make the test wait on the build.
        "test:\n\tcargo build ; cargo nextest run\n",
        "test:\n\tcargo build || cargo nextest run\n",
        # An ignored error leaves the test to run on a failed build.
        "test:\n\t-cargo build\n\tcargo nextest run\n",
        # Make expands a target name where it reads the rule, so a variable set
        # further down does not name `prewarm` here.
        (
            ".PHONY: test prewarm\npre$(T):\n\tcargo build\nT := warm\n"
            "test: prewarm\n\tcargo nextest run\n"
        ),
        # A name that settles to `test` gives it a second rule, which the reader
        # does not combine.
        (
            "T := test\n.PHONY: prepare test\nprepare:\n\tcargo build\n"
            "$(T): prepare\ntest:\n\tcargo nextest run\n"
        ),
        # A conditional build may not run.
        'test:\n\tif [ -n "$(X)" ]; then cargo build; fi\n\tcargo nextest run\n',
        # A prerequisite that only checks tools, formats or lints.
        "test: tools\n\tcargo nextest run\n\ntools:\n\tcargo fmt --check\n",
        # `cargo check` and clippy leave only metadata, which a build recompiles.
        "test: tools\n\tcargo nextest run\n\ntools:\n\tcargo check --all-targets\n",
        "test: tools\n\tcargo nextest run\n\ntools:\n\tcargo clippy\n",
        # A target only a different target reaches.
        "test:\n\tcargo nextest run\n\nprepare:\n\tcargo build\n",
        # A recursive make into another directory reads another Makefile.
        (
            "test:\n\tmake -C sub prepare && cargo nextest run\n\n"
            "prepare:\n\tcargo build\n"
        ),
        # `.ONESHELL` changes what a failed line does to the next.
        ".ONESHELL:\ntest:\n\tcargo build\n\tcargo nextest run\n",
        # A build after `||` runs only when the command before it failed, on the
        # test line and on an earlier line.
        "test:\n\ttrue || cargo build && cargo nextest run\n",
        "test:\n\ttrue || cargo build\n\tcargo nextest run\n",
        # A target that tests before it builds pays for the build inside the run.
        (
            "test: prepare\n\tcargo nextest run\n\n"
            "prepare:\n\tcargo nextest run\n\tcargo build\n"
        ),
        (
            "test: prepare\n\tcargo nextest run\n\n"
            "prepare:\n\tcargo nextest run && cargo build\n"
        ),
        # Sibling prerequisites are not ordered: `make -j` runs them together.
        (
            "test: run-tests prepare\n\tcargo nextest run\n\n"
            "run-tests:\n\tcargo nextest run\n\nprepare:\n\tcargo build\n"
        ),
        # The reader follows only the forms it models exactly; each class of
        # shape it does not model is not preparation. Make options that touch,
        # ask or ignore errors instead of running a build.
        "test:\n\tmake -t prepare && cargo nextest run\n\nprepare:\n\tcargo build\n",
        (
            "test:\n\tmake --touch prepare && cargo nextest run\n\n"
            "prepare:\n\tcargo build\n"
        ),
        "test:\n\tmake -q prepare && cargo nextest run\n\nprepare:\n\tcargo build\n",
        "test:\n\tmake -i prepare && cargo nextest run\n\nprepare:\n\tcargo build\n",
        # Group goals: `make -j` runs them together, so none is ordered first.
        (
            "test:\n\tmake prepare lint && cargo nextest run\n\n"
            "prepare:\n\tcargo build\n\nlint:\n\tcargo fmt\n"
        ),
        # A `VAR=value` override on the make line can change what the goal does.
        (
            "CARGO ?= cargo\ntest:\n\tmake CARGO=true prepare && cargo nextest run\n\n"
            "prepare:\n\t$(CARGO) build\n"
        ),
        # A goal the shell computes names no target the reader can see.
        (
            "test:\n\tmake $(shell echo prepare) && cargo nextest run\n\n"
            "prepare:\n\tcargo build\n"
        ),
        # Prerequisites that read two ways (a probe) are not read per reading.
        (
            "PRE := $(if $(shell cargo nextest --version 2>/dev/null),"
            "prepare,other)\n\n"
            "test: $(PRE)\n\tcargo nextest run\n\n"
            "prepare:\n\tcargo build\n\nother:\n\tcargo fmt\n"
        ),
        # A sibling that reaches the tests through a make goal tests unprepared.
        (
            "test: warm gate\n\tcargo nextest run\n\nwarm:\n\tcargo build\n\n"
            "gate:\n\t$(MAKE) run-tests\n\nrun-tests:\n\tcargo nextest run\n"
        ),
        # A recipe build does not prepare when a prerequisite tests before it.
        (
            "test: run-tests\n\tcargo build\n\tcargo nextest run\n\n"
            "run-tests:\n\tcargo nextest run\n"
        ),
        # A make goal that may test runs before the build that follows it.
        (
            "test:\n\tmake run-tests && cargo build && cargo nextest run\n\n"
            "run-tests:\n\tcargo nextest run\n"
        ),
        # A prerequisite the expansion left open may be a target that tests.
        (
            "RUN := $(shell printf run-tests)\n"
            "test: warm gate\n\tcargo nextest run\n\nwarm:\n\tcargo build\n\n"
            "gate: $(RUN)\n\nrun-tests:\n\tcargo nextest run\n"
        ),
        # Separate rules for one target combine their prerequisites, so the
        # second rule's build is not ordered before the first's tests.
        (
            "test: run-tests\ntest: warm\n\tcargo nextest run\n\n"
            "run-tests:\n\tcargo nextest run\n\nwarm:\n\tcargo build\n"
        ),
        # A conditional rule may not be the active one.
        (
            "test: prepare\n\tcargo nextest run\n\n"
            "ifeq ($(RUN_PREP),yes)\nprepare:\n\tcargo build\n"
            "else\nprepare:\n\t@true\nendif\n"
        ),
        # A `.PHONY` list that reads in alternative ways declares nothing.
        (
            "PHONY_GOAL := $(if $(shell rustc --version 2>/dev/null),other,prepare)\n"
            "test: prepare\n\tcargo nextest run\n\nprepare:\n\tcargo build\n"
            ".PHONY: test $(PHONY_GOAL)\n"
        ),
        # A `.PHONY` declaration inside a conditional may not be the active one.
        (
            ".PHONY: test\nifeq ($(RUN_PREP),yes)\n.PHONY: prepare\nendif\n"
            "test: prepare\n\tcargo nextest run\n\nprepare:\n\tcargo build\n"
        ),
        # A conditional preparation rule on its own (one rule, no `else`).
        (
            "test: prepare\n\tcargo nextest run\n\n"
            "ifeq ($(RUN_PREP),yes)\nprepare:\n\tcargo build\nendif\n"
        ),
        # A double-colon rule runs independently of the target's other rules.
        "test: prepare\n\tcargo nextest run\n\nprepare::\n\tcargo build\n",
        # `.IGNORE` and `MAKEFLAGS` let the tests run after a failed build.
        (
            ".IGNORE: prepare\n"
            "test: prepare\n\tcargo nextest run\n\nprepare:\n\tcargo build\n"
        ),
        ".IGNORE:\ntest: prepare\n\tcargo nextest run\n\nprepare:\n\tcargo build\n",
        (
            "MAKEFLAGS += -i\n"
            "test: prepare\n\tcargo nextest run\n\nprepare:\n\tcargo build\n"
        ),
        # An included file may hold `.IGNORE`; an expanded target name may be a
        # special target; `--help` compiles nothing.
        (
            "SPECIAL := .IGNORE\n$(SPECIAL): prepare\n"
            "test: prepare\n\tcargo nextest run\n\nprepare:\n\tcargo build\n"
        ),
        "test:\n\tcargo build --help && cargo nextest run\n",
        "test:\n\tcargo test --no-run --help && cargo nextest run\n",
        "test:\n\tcargo nextest run --no-run --help && cargo nextest run\n",
        "test:\n\tcargo nextest archive --help && cargo nextest run\n",
        # A compile-only command after the test prepares nothing for it.
        "test:\n\tcargo nextest run\n\tcargo nextest run --no-run\n",
        # `make -f` reads another Makefile and `make -n` runs nothing.
        (
            "test:\n\tmake -f alternate.mk prepare && cargo nextest run\n\n"
            "prepare:\n\tcargo build\n"
        ),
        ("test:\n\tmake -n prepare && cargo nextest run\n\nprepare:\n\tcargo build\n"),
        (
            "test:\n\tmake --dry-run prepare && cargo nextest run\n\n"
            "prepare:\n\tcargo build\n"
        ),
    ],
)
def test_a_compile_that_does_not_gate_the_tests_prepares_nothing(
    makefile: str, tmp_path: pathlib.Path
) -> None:
    """A build the test does not wait on, or may skip, is not a declaration."""
    checkout(tmp_path, makefile)
    assert reported(tmp_path)


def test_an_included_file_may_hold_what_the_reader_cannot_see(
    tmp_path: pathlib.Path,
) -> None:
    """An include can add `.IGNORE`, so a Makefile that includes is not read."""
    makefile = "test: prepare\n\tcargo nextest run\n\nprepare:\n\tcargo build\n"
    plain, including = tmp_path / "plain", tmp_path / "including"
    checkout(plain, makefile)
    assert makefile_prepares(plain)
    checkout(including, f"include flags.mk\n{makefile}")
    put(including, "flags.mk", ".IGNORE: prepare\n")
    assert not makefile_prepares(including)
    # The parser does not report an include that follows a rule, so the text
    # scan is what catches this one.
    after_rule = tmp_path / "after-rule"
    checkout(after_rule, f"{makefile}\ninclude flags.mk\n")
    put(after_rule, "flags.mk", ".IGNORE: prepare\n")
    assert not makefile_prepares(after_rule)


def test_a_target_not_declared_phony_may_be_skipped_so_it_prepares_nothing(
    tmp_path: pathlib.Path,
) -> None:
    """A file named like the target makes Make skip its recipe."""
    checkout(
        tmp_path,
        "test: prepare\n\tcargo nextest run\n\nprepare:\n\tcargo build\n",
        is_phony=False,
    )
    assert reported(tmp_path)


def test_a_preparation_that_loops_back_to_the_test_target_is_not_followed_forever(
    tmp_path: pathlib.Path,
) -> None:
    """A cycle of targets ends as not prepared rather than looping."""
    checkout(
        tmp_path,
        "test: prepare\n\tcargo nextest run\n\nprepare: other\n\nother: prepare\n",
    )
    assert reported(tmp_path)


def test_two_readings_of_one_line_both_reach_the_same_preparing_target(
    tmp_path: pathlib.Path,
) -> None:
    """A target proved by the first reading is still proved for the second."""
    probe = (
        "CARGO ?= cargo\n"
        "TEST_CMD := $(if $(shell $(CARGO) nextest --version 2>/dev/null),"
        "nextest run,test)\n"
    )
    checkout(
        tmp_path,
        f"{probe}\ntest:\n\tmake prepare && cargo $(TEST_CMD)\n\n"
        "prepare:\n\tcargo build\n",
    )
    assert not reported(tmp_path)


def test_a_runner_probe_is_prepared_in_both_branches(tmp_path: pathlib.Path) -> None:
    """A line that picks its runner by probing has the build before both."""
    probe = (
        "CARGO ?= cargo\n"
        "TEST_CMD := $(if $(shell $(CARGO) nextest --version 2>/dev/null),"
        "nextest run,test)\n"
    )
    checkout(tmp_path, f"{probe}\ntest:\n\t$(CARGO) build && $(CARGO) $(TEST_CMD)\n")
    assert not reported(tmp_path)
