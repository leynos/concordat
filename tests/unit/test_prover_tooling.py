"""Audit prover tooling through real checkouts and the real policy."""

from __future__ import annotations

import typing as typ

import pytest

from concordat.errors import OperationalRuleError
from concordat.rules import runner
from concordat.rules.prover_tooling_envelope import build_prover_tooling_envelope

if typ.TYPE_CHECKING:
    import pathlib

RULE_ID = "rust-prover-tooling"
REF = "592558f7183b554fc749696246984c5d7300d029"
OLD = "9c4a27a86c678ceac1155404f45d95ee4a14a172"
ACTION = "leynos/shared-actions/.github/actions/install-prover-tools@"
SHA = "a0519a1476ab769ca7b8badfa794f3751213f1ea"
SOURCE = f"git+https://github.com/leynos/rust-prover-tools@{REF}"
MAKEFILE = f"""PROVER_TOOLS_SOURCE ?= {SOURCE}
PROVER_TOOLS ?= uvx --from $(PROVER_TOOLS_SOURCE) prover-tools

verus-install:
\t$(PROVER_TOOLS) verus install --repo-root .

verus:
\t$(PROVER_TOOLS) verus run --repo-root . --proof-file verus/lib.rs
"""
INSTALL_STEP = f"      - uses: {ACTION}{SHA}\n        with:\n          tools: verus\n"


def workflow(*steps: str) -> str:
    """Return a one-job workflow made of *steps*."""
    body = "".join(steps)
    return (
        "name: Verus\non: push\njobs:\n  verify:\n    runs-on: ubuntu-latest\n"
        f"    steps:\n      - uses: actions/checkout@v7\n{body}"
    )


def run_step(command: str) -> str:
    """Return one workflow step that runs *command*."""
    return f"      - run: {command}\n"


def put(root: pathlib.Path, relative: str, text: str) -> None:
    """Write *text* to *relative* under *root*, creating directories."""
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def checkout(
    root: pathlib.Path,
    *,
    makefile: str | None = MAKEFILE,
    steps: tuple[str, ...] = (INSTALL_STEP, "      - run: make verus\n"),
    version: bool = True,
) -> pathlib.Path:
    """Write a compliant Verus repository, overridable piece by piece."""
    if makefile is not None:
        put(root, "Makefile", makefile)
    if version:
        put(root, "tools/verus/VERSION", "0.2026.09.06.8dea4a2\n")
    put(root, ".github/workflows/verus.yml", workflow(*steps))
    return root


def ids(root: pathlib.Path) -> set[str]:
    """Return the rule ids the real policy reports for a checkout."""
    return {finding.rule_id for finding in runner.run_rule(RULE_ID, root).findings}


def test_the_reference_shape_is_compliant(tmp_path: pathlib.Path) -> None:
    """A Makefile-variable pin, the shared action and run-only targets pass."""
    assert ids(checkout(tmp_path)) == set()


def test_a_ref_file_pin_with_a_shell_substitution_is_compliant(
    tmp_path: pathlib.Path,
) -> None:
    """The REF file is the pin where the Makefile reads it with `$(shell ...)`."""
    substitution = "$(shell cat tools/rust-prover-tools/REF)"
    makefile = MAKEFILE.replace(
        SOURCE, f"git+https://github.com/leynos/rust-prover-tools@{substitution}"
    )
    root = checkout(tmp_path, makefile=makefile)
    put(root, "tools/rust-prover-tools/REF", f"repository: x\nref: {REF}\nverify: y\n")
    assert ids(root) == set()


def test_a_second_pin_is_noncompliant(tmp_path: pathlib.Path) -> None:
    """The REF file and the Makefile must name the same single commit."""
    root = checkout(tmp_path)
    put(root, "tools/rust-prover-tools/REF", f"{OLD}\n")
    assert ids(root) == {"PT-001"}


def test_a_missing_pin_is_noncompliant(tmp_path: pathlib.Path) -> None:
    """A repository that uses a prover has to pin the tool."""
    root = checkout(tmp_path, makefile=MAKEFILE.replace(SOURCE, "git+https://x/y"))
    assert ids(root) == {"PT-001"}


def test_a_step_that_makes_a_target_with_an_installing_prerequisite_is_noncompliant(
    tmp_path: pathlib.Path,
) -> None:
    """`make verus` reaches an installer when the target depends on one."""
    makefile = MAKEFILE.replace("verus:\n", "verus: verus-install\n")
    assert ids(checkout(tmp_path, makefile=makefile)) == {"PT-002"}


def test_a_direct_installer_step_is_noncompliant(tmp_path: pathlib.Path) -> None:
    """A `run` step that installs a prover is not the shared action."""
    steps = (run_step(f"uvx --from {SOURCE} prover-tools kani install"),)
    assert ids(checkout(tmp_path, steps=steps)) == {"PT-002"}


def test_a_printed_installer_is_not_an_installer(tmp_path: pathlib.Path) -> None:
    """Naming an installer in `printf` runs nothing."""
    steps = (INSTALL_STEP, run_step("printf 'prover-tools verus install\\n'"))
    assert ids(checkout(tmp_path, steps=steps)) == set()


def test_an_action_pinned_by_a_tag_is_noncompliant(tmp_path: pathlib.Path) -> None:
    """The shared action has to be pinned by a full SHA."""
    steps = (f"      - uses: {ACTION}v1\n        with:\n          tools: verus\n",)
    assert ids(checkout(tmp_path, steps=steps)) == {"PT-003"}


@pytest.mark.parametrize(
    ("relative", "text"),
    [
        ("scripts/install_kani.py", "print('hi')\n"),
        ("tools/verus/setup-verus.sh", "echo hi\n"),
        ("scripts/get.sh", "#!/bin/sh\nprover-tools verus install\n"),
        (
            "scripts/fetch.py",
            "URL = 'https://github.com/verus-lang/verus/releases/download/x'\n",
        ),
    ],
)
def test_a_local_installer_is_noncompliant(
    tmp_path: pathlib.Path, relative: str, text: str
) -> None:
    """A script is an installer by its name, its commands or its download URL."""
    root = checkout(tmp_path)
    put(root, relative, text)
    assert ids(root) == {"PT-004"}


@pytest.mark.parametrize(
    ("relative", "text"),
    [
        ("scripts/run_proofs.sh", "prover-tools verus run --repo-root .\n"),
        (
            "scripts/note.py",
            "# https://github.com/verus-lang/verus/releases\nprint(1)\n",
        ),
        ("scripts/fixtures/install_kani.py", "print('hi')\n"),
    ],
)
def test_a_script_that_only_runs_or_mentions_is_compliant(
    tmp_path: pathlib.Path, relative: str, text: str
) -> None:
    """Running proofs, a comment, or a fixture is not a local installer."""
    root = checkout(tmp_path)
    put(root, relative, text)
    assert ids(root) == set()


def test_a_test_of_installation_is_noncompliant(tmp_path: pathlib.Path) -> None:
    """A test file named for prover installation is refused."""
    root = checkout(tmp_path)
    put(root, "tests/verus_install_test.rs", "fn main() {}\n")
    assert ids(root) == {"PT-005"}


def test_a_repository_without_a_prover_is_not_audited(tmp_path: pathlib.Path) -> None:
    """Nothing here installs, runs or pins a prover."""
    put(tmp_path, "Makefile", "build:\n\tcargo build\n")
    put(tmp_path, ".github/workflows/ci.yml", workflow(run_step("make build")))
    assert ids(tmp_path) == set()


def test_a_repository_with_proof_code_and_no_wiring_is_not_audited(
    tmp_path: pathlib.Path,
) -> None:
    """Proof code alone is not use: nothing on main installs or runs a prover."""
    put(tmp_path, "src/lib.rs", "#[kani::proof]\nfn h() {}\n")
    assert ids(tmp_path) == set()


EXCEPTION = """language:
  rust:
    prover_tools:
      exceptions:
        - clauses: [PT-001, PT-002]
          pin: 9c4a27a8
          record: docs/adrs/adr-0011.md
          rationale: The provenance records name 9c4a27a8.
          review_trigger: Revisit at the Kani recapture.
"""


def excepted(root: pathlib.Path, *, record: bool = True, pin: str = OLD) -> None:
    """Make *root* a repository on the old pin that installs Kani itself."""
    checkout(root, steps=(run_step("prover-tools kani install"),))
    put(root, "tools/rust-prover-tools/REF", f"ref: {pin}\n")
    put(root, "Makefile", MAKEFILE.replace(SOURCE, "git+https://x/y"))
    put(root, ".concordat", EXCEPTION)
    if record:
        put(root, "docs/adrs/adr-0011.md", "# ADR\n")


def test_an_exception_in_force_clears_its_clauses(tmp_path: pathlib.Path) -> None:
    """The pin matches by prefix and the record exists."""
    excepted(tmp_path)
    assert ids(tmp_path) == set()


def test_an_exception_whose_pin_moved_waives_nothing(tmp_path: pathlib.Path) -> None:
    """Moving the pin reopens the question."""
    excepted(tmp_path, pin=REF)
    assert ids(tmp_path) == {"EX-001", "PT-002"}


def test_an_exception_without_its_record_waives_nothing(tmp_path: pathlib.Path) -> None:
    """The decision record has to exist."""
    excepted(tmp_path, record=False)
    assert ids(tmp_path) == {"EX-001", "PT-001", "PT-002"}


def test_a_malformed_exception_is_an_operational_failure(
    tmp_path: pathlib.Path,
) -> None:
    """A declaration that is not a list of mappings stops the run."""
    checkout(tmp_path)
    put(
        tmp_path,
        ".concordat",
        "language:\n  rust:\n    prover_tools:\n      exceptions: 3\n",
    )
    with pytest.raises(OperationalRuleError):
        build_prover_tooling_envelope(tmp_path)


def test_an_unreadable_workflow_is_indeterminate(tmp_path: pathlib.Path) -> None:
    """A workflow that does not decode cannot clear anything."""
    root = checkout(tmp_path)
    put(root, ".github/workflows/broken.yml", "jobs: [unclosed\n")
    result = runner.run_rule(RULE_ID, root)
    assert {(f.rule_id, f.verdict) for f in result.findings} == {
        ("EN-001", "indeterminate")
    }


def test_the_envelope_records_the_facts_it_judges(tmp_path: pathlib.Path) -> None:
    """The envelope names pins, version files and the Makefile state."""
    envelope = build_prover_tooling_envelope(checkout(tmp_path))
    assert envelope["kind"] == "policy-input/rust-prover-tooling"
    assert envelope["makefile"] == {"state": "ok", "error": None}
    assert envelope["version_files"] == ["tools/verus/VERSION"]
    assert [pin["value"] for pin in envelope["pins"]] == [REF]
    assert envelope["runs_prover"] is True


def test_a_bare_make_step_follows_the_first_target(tmp_path: pathlib.Path) -> None:
    """With no target named, `make` builds the Makefile's first ordinary target."""
    makefile = "all: verus-install verus\n\n" + MAKEFILE
    steps = (INSTALL_STEP, run_step("make"))
    assert ids(checkout(tmp_path, makefile=makefile, steps=steps)) == {"PT-002"}


def test_a_bare_make_step_follows_the_declared_default_goal(
    tmp_path: pathlib.Path,
) -> None:
    """`.DEFAULT_GOAL` overrides the first target."""
    makefile = MAKEFILE + ".DEFAULT_GOAL := verus-install\n"
    steps = (INSTALL_STEP, run_step("make"))
    assert ids(checkout(tmp_path, makefile=makefile, steps=steps)) == {"PT-002"}


def test_a_mutable_ref_file_value_is_noncompliant(tmp_path: pathlib.Path) -> None:
    """A `ref:` that is a branch name is not the pinned revision."""
    root = checkout(tmp_path, makefile=MAKEFILE.replace(SOURCE, "git+https://x/y"))
    put(root, "tools/rust-prover-tools/REF", "ref: main\n")
    assert ids(root) == {"PT-001"}


def test_a_revision_with_a_suffix_is_noncompliant(tmp_path: pathlib.Path) -> None:
    """`<sha>-patched` is a different reference, not the revision it starts with."""
    makefile = MAKEFILE.replace(SOURCE, f"{SOURCE}-patched")
    assert ids(checkout(tmp_path, makefile=makefile)) == {"PT-001"}


def test_an_inline_pin_in_a_recipe_is_compared(tmp_path: pathlib.Path) -> None:
    """A recipe that names its own `rust-prover-tools` revision counts as a pin."""
    source = f"git+https://github.com/leynos/rust-prover-tools@{OLD}"
    inline = f"\tuvx --from {source} prover-tools verus run\n"
    makefile = MAKEFILE + "\nextra:\n" + inline
    assert ids(checkout(tmp_path, makefile=makefile)) == {"PT-001"}


def test_a_step_that_runs_an_installer_script_is_an_installer_step(
    tmp_path: pathlib.Path,
) -> None:
    """Running a local installer script is PT-002 as well as the script's PT-004."""
    root = checkout(tmp_path, steps=(INSTALL_STEP, run_step("bash scripts/get.sh")))
    put(root, "scripts/get.sh", "#!/bin/sh\nprover-tools verus install\n")
    assert ids(root) == {"PT-002", "PT-004"}


def test_a_release_page_link_is_not_a_download(tmp_path: pathlib.Path) -> None:
    """Only a release-download URL makes a Python script an installer."""
    root = checkout(tmp_path)
    put(root, "scripts/links.py", "HOME = 'https://github.com/verus-lang/verus'\n")
    assert ids(root) == set()


def test_a_ref_file_that_leaves_the_checkout_stops_the_run(
    tmp_path: pathlib.Path,
) -> None:
    """A REF symlink to a file outside the checkout is an operational failure."""
    outside = tmp_path / "outside.txt"
    outside.write_text(f"ref: {REF}\n", encoding="utf-8")
    root = tmp_path / "repo"
    checkout(root)
    link = root / "tools/rust-prover-tools/REF"
    link.parent.mkdir(parents=True)
    link.symlink_to(outside)
    with pytest.raises(OperationalRuleError):
        build_prover_tooling_envelope(root)
