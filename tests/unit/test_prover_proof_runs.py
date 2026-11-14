"""Decide whether CI runs a repository's proofs (PT-006), through real checkouts."""

from __future__ import annotations

import typing as typ

import pytest

from concordat.rules import runner
from concordat.rules.prover_commands import is_substantive, runs_proofs

if typ.TYPE_CHECKING:
    import pathlib

RULE_ID = "rust-prover-tooling"
REF = "592558f7183b554fc749696246984c5d7300d029"
ACTION = "leynos/shared-actions/.github/actions/install-prover-tools@"
SHA = "a0519a1476ab769ca7b8badfa794f3751213f1ea"
SOURCE = f"git+https://github.com/leynos/rust-prover-tools@{REF}"
HARNESS = "#[cfg(kani)]\nmod proofs {\n    #[kani::proof]\n    fn holds() {}\n}\n"
INSTALL = f"      - uses: {ACTION}{SHA}\n        with:\n          tools: kani\n"


def workflow(*steps: str) -> str:
    """Return a one-job workflow made of *steps*."""
    return (
        "name: Proofs\non: push\njobs:\n  verify:\n    runs-on: ubuntu-latest\n"
        "    steps:\n      - uses: actions/checkout@v7\n" + "".join(steps)
    )


def run(command: str) -> str:
    """Return one workflow step that runs *command*."""
    return f"      - run: {command}\n"


def put(root: pathlib.Path, relative: str, text: str) -> None:
    """Write *text* to *relative* under *root*, creating directories."""
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def repository(
    root: pathlib.Path, *, makefile: str, steps: tuple[str, ...], extra: dict[str, str]
) -> pathlib.Path:
    """Write a pinned repository with a harness, a Makefile and a workflow."""
    put(root, "tools/rust-prover-tools/REF", f"repository: x\nref: {REF}\nverify: y\n")
    put(root, "src/kani.rs", HARNESS)
    put(root, "Makefile", makefile)
    put(root, ".github/workflows/proofs.yml", workflow(*steps))
    for relative, text in extra.items():
        put(root, relative, text)
    return root


def ids(root: pathlib.Path) -> set[str]:
    """Return the rule ids the real policy reports for a checkout."""
    return {finding.rule_id for finding in runner.run_rule(RULE_ID, root).findings}


def audit(
    tmp_path: pathlib.Path,
    *,
    makefile: str = "kani:\n\tcargo kani\n",
    steps: tuple[str, ...] = (INSTALL, run("make kani")),
    extra: dict[str, str] | None = None,
) -> set[str]:
    """Return the findings for a repository built from the given pieces."""
    return ids(repository(tmp_path, makefile=makefile, steps=steps, extra=extra or {}))


@pytest.mark.parametrize(
    "argv",
    [
        ["cargo", "kani"],
        ["cargo", "kani", "--harness", "h"],
        ["cargo-kani", "kani", "--workspace"],
        ["kani-driver", "kani"],
        ["env", "RUSTFLAGS=-D warnings", "cargo-kani", "kani"],
        ["prover-tools", "kani", "exec", "--installation", "k"],
        ["prover-tools", "verus", "run", "--proof-file", "a.rs"],
        ["$X", "verus", "run"],
        ["uvx", "--from", "x", "prover-tools", "verus", "run"],
        ["verus", "--crate-type=lib", "proof.rs"],
        ["cargo", "verus", "verify"],
        ["cargo-verus", "verus", "focus"],
    ],
)
def test_these_commands_run_proofs(argv: list[str]) -> None:
    """Commands that verify proof code count."""
    assert runs_proofs(argv)


@pytest.mark.parametrize(
    "argv",
    [
        ["cargo", "kani", "setup"],
        ["cargo", "kani", "list"],
        ["cargo", "kani", "--version"],
        ["cargo-kani", "--version"],
        ["prover-tools", "kani", "install"],
        ["prover-tools", "kani", "check-version"],
        ["prover-tools", "verus", "install"],
        ["verus", "--version"],
        ["printf", "cargo kani"],
        ["echo", "cargo", "kani"],
        ["git", "commit", "-m", "verus run"],
        ["$X", "kani", "install"],
        ["$X", "verus", "check-version"],
    ],
)
def test_these_commands_do_not_run_proofs(argv: list[str]) -> None:
    """Installing, checking, listing and printing do not."""
    assert not runs_proofs(argv)


def test_a_recipe_is_substantive_only_when_it_does_work() -> None:
    """Printing, installing and version checks are not work."""
    assert is_substantive(["cargo", "test", "--test", "bdd"])
    assert not is_substantive(["printf", "skipped"])
    assert not is_substantive(["prover-tools", "kani", "install"])
    assert not is_substantive(["prover-tools", "kani", "check-version"])
    assert not is_substantive(["cd", "tools"])


def test_a_make_target_that_runs_cargo_kani_runs_the_proofs(
    tmp_path: pathlib.Path,
) -> None:
    """The reference shape: install through the action, then make the target."""
    assert audit(tmp_path) == set()


def test_proof_code_with_no_run_step_is_noncompliant(tmp_path: pathlib.Path) -> None:
    """Installing the prover is not running the proofs."""
    assert audit(tmp_path, steps=(INSTALL, run("echo installed"))) == {"PT-006"}


def test_a_prerequisite_that_runs_the_proofs_counts(tmp_path: pathlib.Path) -> None:
    """The step makes a target whose prerequisite's recipe holds the run."""
    makefile = "all: kani-run\n\t@echo done\n\nkani-run:\n\tcargo kani --workspace\n"
    assert audit(tmp_path, makefile=makefile, steps=(INSTALL, run("make all"))) == set()


def test_a_direct_cargo_kani_step_counts(tmp_path: pathlib.Path) -> None:
    """A workflow step may run the prover itself."""
    assert audit(tmp_path, steps=(INSTALL, run("cargo kani --workspace"))) == set()


def test_a_step_that_only_lists_harnesses_does_not_count(
    tmp_path: pathlib.Path,
) -> None:
    """`cargo kani list` enumerates; it verifies nothing."""
    assert audit(tmp_path, steps=(INSTALL, run("cargo kani list"))) == {"PT-006"}


def test_a_make_variable_in_a_recipe_is_expanded(tmp_path: pathlib.Path) -> None:
    """A recipe that runs `$(RUN)` runs what the variable stands for."""
    makefile = (
        f"PROVER_TOOLS ?= uvx --from {SOURCE} prover-tools\n"
        "VERUS_RUN ?= env -u RUSTUP_TOOLCHAIN $(PROVER_TOOLS) verus run\n\n"
        "verus:\n\t$(VERUS_RUN) --proof-file verus/lib.rs\n"
    )
    assert (
        audit(tmp_path, makefile=makefile, steps=(INSTALL, run("make verus"))) == set()
    )


def test_a_shell_script_that_runs_the_proofs_counts(tmp_path: pathlib.Path) -> None:
    """A step that runs a repository script follows the script's commands."""
    script = "#!/bin/sh\ncargo kani --workspace\n"
    audit_ids = audit(
        tmp_path,
        steps=(INSTALL, run("./scripts/prove.sh")),
        extra={"scripts/prove.sh": script},
    )
    assert audit_ids == set()


def test_a_python_script_that_builds_the_command_counts(
    tmp_path: pathlib.Path,
) -> None:
    """A script that holds the command as a list literal runs the proofs."""
    script = 'import subprocess\nsubprocess.run(["cargo", "kani", "--workspace"])\n'
    audit_ids = audit(
        tmp_path,
        steps=(INSTALL, run("python3 scripts/prove.py")),
        extra={"scripts/prove.py": script},
    )
    assert audit_ids == set()


def test_a_script_that_only_names_the_command_does_not_count(
    tmp_path: pathlib.Path,
) -> None:
    """Printing the command is not running it."""
    script = "#!/bin/sh\necho 'cargo kani'\n"
    audit_ids = audit(
        tmp_path,
        steps=(INSTALL, run("./scripts/prove.sh")),
        extra={"scripts/prove.sh": script},
    )
    assert audit_ids == {"PT-006"}


def test_a_generator_run_through_a_test_suite_counts_after_the_shared_install(
    tmp_path: pathlib.Path,
) -> None:
    """A generator's harnesses are verified inside a test suite (theoremc's shape).

    No recipe names `cargo kani`, so the rule accepts a workflow that installs
    through the shared action and then makes a substantive prover-named target.
    """
    makefile = "kani:\n\tTHEOREM_REQUIRE_KANI=1 cargo test --test bdd -- kani\n"
    extra = {
        "crates/macros/Cargo.toml": "[lib]\nproc-macro = true\n",
        "crates/macros/src/lib.rs": (
            "pub fn emit() { quote::quote! { #[kani::proof] fn h() {} }; }\n"
        ),
    }
    assert audit(tmp_path, makefile=makefile, extra=extra) == set()


def test_a_prover_named_target_that_only_prints_does_not_count(
    tmp_path: pathlib.Path,
) -> None:
    """A placeholder target that prints and exits is the failure PT-006 names."""
    makefile = "kani:\n\t@printf 'FORMAL-SKIP: no harnesses\\n'\n"
    assert audit(tmp_path, makefile=makefile) == {"PT-006"}


def test_a_prover_named_target_does_not_count_without_the_shared_install(
    tmp_path: pathlib.Path,
) -> None:
    """The substantive-target allowance is for workflows that install the prover."""
    makefile = "kani:\n\tcargo test --test bdd -- kani\n"
    assert audit(tmp_path, makefile=makefile, steps=(run("make kani"),)) == {"PT-006"}


def test_an_unparseable_workflow_makes_the_answer_unknown(
    tmp_path: pathlib.Path,
) -> None:
    """A workflow the rule cannot read might run the proofs."""
    root = repository(
        tmp_path, makefile="kani:\n\tcargo kani\n", steps=(INSTALL,), extra={}
    )
    put(root, ".github/workflows/proofs.yml", "jobs: [unclosed\n")
    assert ids(root) == {"EN-001"}


def test_proof_code_nobody_runs_is_not_waivable(tmp_path: pathlib.Path) -> None:
    """An exception can waive the install clauses, never PT-006."""
    exception = (
        "language:\n  rust:\n    prover_tools:\n      exceptions:\n"
        "        - clauses: [PT-006]\n          pin: 592558f7\n"
        "          record: docs/adr.md\n          rationale: slow\n"
        "          review_trigger: when fast\n"
    )
    root = repository(
        tmp_path,
        makefile="kani:\n\tcargo kani\n",
        steps=(INSTALL, run("echo installed")),
        extra={".concordat": exception, "docs/adr.md": "# adr\n"},
    )
    assert {"PT-006", "EX-001"} <= ids(root)
