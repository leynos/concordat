"""Judge each run of a reused `Command`, not the ledger it was left as.

A bound command may run more than once, and each run carries whatever had been
set at that point. The rule therefore keeps one fact per distinct state and
target configuration. These cases cover the argument additions that make two
runs build in different places, and the additions that leave one fact.
"""

from __future__ import annotations

import typing as typ

from concordat.rules.nested_cargo_cold_envelope import (
    build_nested_cargo_cold_envelope,
)
from tests.unit.test_nested_cargo_cold_rule import (
    SETUP_SCRIPT,
    findings,
    profile,
    put,
)

if typ.TYPE_CHECKING:
    import pathlib

REUSED_COMMAND = """\
fn build_twice(shared: &std::path::Path) {
    let scratch = tempfile::tempdir().unwrap();
    let mut cmd = std::process::Command::new("cargo");
    cmd.arg("build");
    cmd.env("CARGO_TARGET_DIR", shared);
    cmd.status().unwrap();
    cmd.env("CARGO_TARGET_DIR", scratch.path());
    cmd.status().unwrap();
}
"""

REUSED_PROGRAM = """\
fn run_twice(exe: &str) {
    let scratch = tempfile::tempdir().unwrap();
    let mut command = std::process::Command::new(exe);
    command.env("CARGO_TARGET_DIR", scratch.path());
    command.output().unwrap();
    command.env_remove("CARGO_TARGET_DIR");
    command.output().unwrap();
}
"""


def test_a_reused_program_is_read_at_its_first_run(tmp_path: pathlib.Path) -> None:
    """The first run built into a temporary target, whatever the last run did."""
    put(tmp_path, "tests/program.rs", REUSED_PROGRAM)
    assert ("NC-009", "tests/program.rs") in profile(tmp_path)


def test_a_reused_command_is_judged_at_each_run(tmp_path: pathlib.Path) -> None:
    """The second run builds into a temporary directory, whatever the first did."""
    put(tmp_path, "tests/twice.rs", REUSED_COMMAND)
    assert ("NC-009", "tests/twice.rs") in profile(tmp_path)


REUSED_COMMAND_UNREADABLE_FLAG = """\
fn build_twice(flag: &str) {
    let mut cmd = std::process::Command::new("cargo");
    cmd.args(["build", "--frozen"]);
    cmd.status().unwrap();
    cmd.arg(flag);
    cmd.status().unwrap();
}
"""

# Two runs with the same evidence start in the same place, so the reader keeps
# one fact for them rather than reporting the same build twice.
REUSED_COMMAND_UNCHANGED = """\
fn build_twice() {
    let mut cmd = std::process::Command::new("cargo");
    cmd.args(["build", "--frozen"]);
    cmd.status().unwrap();
    cmd.status().unwrap();
}
"""

# A conditional argument call may add the target flag on the later path only.
REUSED_COMMAND_CONDITIONAL_ARGUMENTS = """\
fn build_twice(condition: bool, extra: &[&str]) {
    let mut cmd = std::process::Command::new("cargo");
    cmd.args(["build", "--frozen"]);
    cmd.status().unwrap();
    if condition {
        cmd.args(extra);
    }
    cmd.status().unwrap();
}
"""


def prepared_reused_command(root: pathlib.Path, source: str) -> None:
    """Write a reused-command test and a nextest script that prepares it."""
    put(root, "tests/twice.rs", source)
    put(
        root,
        ".config/nextest.toml",
        SETUP_SCRIPT.format(filter="all()", setup="prepare-nested"),
    )


def test_a_reused_command_is_judged_again_after_an_unreadable_argument(
    tmp_path: pathlib.Path,
) -> None:
    """An argument added between runs may select a private target for the second.

    The two runs are both `offline`, but the second carries a word the reader
    cannot read, so it builds somewhere the first one did not prove. Collapsing
    them to one fact would report the first run's inherited target for both.
    """
    prepared_reused_command(tmp_path, REUSED_COMMAND_UNREADABLE_FLAG)
    found = findings(tmp_path)
    assert [(f.rule_id, f.verdict) for f in found] == [("EN-001", "indeterminate")]
    [indeterminate] = found
    assert indeterminate.path == "tests/twice.rs"
    assert indeterminate.line == 2
    assert indeterminate.message == (
        "tests/twice.rs:2 starts a nested build and sets its target directory from "
        "a value the reader cannot follow (an argument call the reader cannot see "
        "may select a target), so whether the directory is private is unknown"
    )


def test_a_reused_command_reports_both_runs_in_order(tmp_path: pathlib.Path) -> None:
    """The first run is inherited and the second unknown, both still offline."""
    prepared_reused_command(tmp_path, REUSED_COMMAND_UNREADABLE_FLAG)
    spawns = build_nested_cargo_cold_envelope(tmp_path)["spawns"]
    assert [
        (spawn["state"], spawn["compiles"], spawn["target_state"]) for spawn in spawns
    ] == [
        ("offline", "yes", "inherited"),
        ("offline", "yes", "unknown"),
    ]


def test_two_unchanged_runs_of_a_reused_command_are_one_fact(
    tmp_path: pathlib.Path,
) -> None:
    """Identical evidence starts the process in the same place, so it is one fact."""
    put(tmp_path, "tests/twice.rs", REUSED_COMMAND_UNCHANGED)
    spawns = build_nested_cargo_cold_envelope(tmp_path)["spawns"]
    assert [(spawn["state"], spawn["target_state"]) for spawn in spawns] == [
        ("offline", "inherited")
    ]


def test_a_conditional_argument_call_between_runs_keeps_both_facts(
    tmp_path: pathlib.Path,
) -> None:
    """A call on only some paths may change the target, so neither run collapses."""
    put(tmp_path, "tests/twice.rs", REUSED_COMMAND_CONDITIONAL_ARGUMENTS)
    spawns = build_nested_cargo_cold_envelope(tmp_path)["spawns"]
    assert [(spawn["state"], spawn["target_state"]) for spawn in spawns] == [
        ("offline", "inherited"),
        ("offline", "unknown"),
    ]


# The first run leaves `--target-dir` waiting, so the literal added for the
# second run is the flag's value rather than a harmless addition.
REUSED_COMMAND_COMPLETED_FLAG = """\
fn build_twice() {
    let mut cmd = std::process::Command::new("cargo");
    cmd.args(["build", "--frozen", "--target-dir"]);
    cmd.status().unwrap();
    cmd.arg("/tmp/nested-target");
    cmd.status().unwrap();
}
"""


def test_a_literal_that_completes_the_flag_is_judged_as_its_own_run(
    tmp_path: pathlib.Path,
) -> None:
    """The second run names a target the first one did not, so both are kept.

    `--target-dir` waits for a value at the end of the first run. The literal
    added before the second supplies it, which changes where the command
    builds; treating the addition as harmless would drop that run.
    """
    put(tmp_path, "tests/twice.rs", REUSED_COMMAND_COMPLETED_FLAG)
    spawns = build_nested_cargo_cold_envelope(tmp_path)["spawns"]
    assert [(spawn["state"], spawn["target_state"]) for spawn in spawns] == [
        ("offline", "inherited"),
        ("offline", "unknown"),
    ]


def test_a_run_that_completes_the_flag_is_reported(
    tmp_path: pathlib.Path,
) -> None:
    """The run that supplies the pending flag's value is indeterminate."""
    prepared_reused_command(tmp_path, REUSED_COMMAND_COMPLETED_FLAG)
    found = findings(tmp_path)
    assert [(f.rule_id, f.verdict, f.line) for f in found] == [
        ("EN-001", "indeterminate", 2)
    ]


# The manifest path moves the default target to another workspace, so the
# second run is not the private build the first one was.
REUSED_COMMAND_MANIFEST_PATH = """\
fn build_twice() {
    let scratch = tempfile::tempdir().unwrap();
    let mut cmd = std::process::Command::new("cargo");
    cmd.current_dir(scratch.path());
    cmd.env_clear();
    cmd.args(["build", "--frozen"]);
    cmd.status().unwrap();
    cmd.arg("--manifest-path=../app/Cargo.toml");
    cmd.status().unwrap();
}
"""


def test_a_literal_manifest_path_between_runs_is_judged_as_its_own_run(
    tmp_path: pathlib.Path,
) -> None:
    """The first run is private; the manifest path sends the second elsewhere."""
    put(tmp_path, "tests/twice.rs", REUSED_COMMAND_MANIFEST_PATH)
    spawns = build_nested_cargo_cold_envelope(tmp_path)["spawns"]
    assert [(spawn["state"], spawn["target_state"]) for spawn in spawns] == [
        ("offline", "private"),
        ("offline", "inherited"),
    ]
