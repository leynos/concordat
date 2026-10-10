"""Specify the revision-list command line: statuses, defaults and error paths."""

from __future__ import annotations

import os
import pathlib
import sys
import typing as typ

import pygit2
import pytest

from concordat.errors import OperationalRuleError
from concordat.rules import packages
from concordat.rules.whitaker_revisions import (
    REFS_KEY,
    replace_refs,
)
from scripts import whitaker_revisions
from tests.unit.whitaker_revisions_support import (
    ACTION_V1,
    History,
    action_files,
    run_main,
)

pytest_plugins = ("tests.unit.whitaker_revisions_fixtures",)


if typ.TYPE_CHECKING:
    import collections.abc as cabc


def test_check_reports_revisions_the_clone_does_not_derive(
    history: History,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`check` fails, naming both directions, when the manifest drifts."""
    root = history.commit(action_files(ACTION_V1), "root")
    real = packages.rule_parameters
    monkeypatch.setattr(
        packages,
        "rule_parameters",
        lambda rule_dir: {**real(rule_dir), "install_whitaker_roots": [root]},
    )

    status = whitaker_revisions.check(pathlib.Path(history.repository.workdir), "main")

    output = capsys.readouterr().out
    assert status == 1
    assert f"missing: {root}" in output
    assert "not derivable:" in output


def test_sync_then_check_round_trips_through_the_command(
    rule_package: tuple[pathlib.Path, str, str],
    history: History,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`check` fails while the manifest lags, and `sync` brings it level."""
    package, root, later = rule_package
    clone = pathlib.Path(history.repository.workdir)

    assert whitaker_revisions.check(clone, "main") == 1
    assert f"missing: {later}" in capsys.readouterr().out
    assert whitaker_revisions.sync(clone, "main") == 0
    assert whitaker_revisions.check(clone, "main") == 0
    listed = packages.rule_parameters(package)[REFS_KEY]
    assert listed == [root, later]
    assert whitaker_revisions.list_revisions(clone, "main") == 0
    assert capsys.readouterr().out.endswith(f"{root}\n{later}\n")


def test_sync_reports_an_unwritable_manifest_as_an_operational_error(
    rule_package: tuple[pathlib.Path, str, str], history: History
) -> None:
    """A manifest that cannot be written is an error, not a raw OSError."""
    package, _root, _later = rule_package
    manifest = package / "rule.yaml"
    manifest.chmod(0o400)
    clone = pathlib.Path(history.repository.workdir)
    try:
        if os.access(manifest, os.W_OK):
            pytest.skip("the manifest stays writable for this user")
        with pytest.raises(OperationalRuleError, match="cannot update"):
            whitaker_revisions.sync(clone, "main")
    finally:
        manifest.chmod(0o600)


def test_the_documented_list_command_is_registered(
    rule_package: tuple[pathlib.Path, str, str],
    history: History,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`whitaker_revisions.py list <clone>` runs, as the docstring documents.

    Cyclopts names a command after its function, so `list_revisions` would be
    `list-revisions` and the documented `list` would be an unknown command. The
    test goes through `main()` rather than calling the function, which is the
    only route that exercises the registered name.
    """
    _package, root, later = rule_package
    clone = str(history.repository.workdir)
    monkeypatch.setattr(
        sys, "argv", ["whitaker_revisions", "list", clone, "--tip", "main"]
    )

    assert whitaker_revisions.main() == 0
    assert capsys.readouterr().out.endswith(f"{root}\n{later}\n")


def test_main_exits_2_for_a_clone_that_is_not_a_repository(
    rule_package: tuple[pathlib.Path, str, str],
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The entry point turns an operational error into status 2."""
    empty = tmp_path_factory.mktemp("not-a-clone")
    monkeypatch.setattr(sys, "argv", ["whitaker_revisions", "check", str(empty)])

    assert whitaker_revisions.main() == 2
    assert "is not a Git repository" in capsys.readouterr().err


def test_main_runs_check_and_sync_with_their_documented_statuses(
    rule_package: tuple[pathlib.Path, str, str],
    history: History,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`check` reports drift with status 1, `sync` fixes it, `check` then passes.

    The tests above call the functions directly; this one goes through `main()`
    with the documented argv, so a command that is not dispatched, or a status
    that is not returned, fails here.
    """
    package, root, later = rule_package
    clone = pathlib.Path(history.repository.workdir)

    assert run_main(monkeypatch, clone, "check") == 1
    assert run_main(monkeypatch, clone, "sync") == 0
    assert packages.rule_parameters(package)[REFS_KEY] == [root, later]
    assert run_main(monkeypatch, clone, "check") == 0


def test_a_git_read_failure_is_a_controlled_error_through_the_cli(
    rule_package: tuple[pathlib.Path, str, str],
    history: History,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An object Git cannot read while walking history exits 2, not a traceback."""
    clone = pathlib.Path(history.repository.workdir)

    def unreadable(*_args: object) -> object:
        """Fail as Git does on a corrupt object."""
        message = "object not found"
        raise pygit2.GitError(message)

    monkeypatch.setattr(
        "concordat.rules.whitaker_revisions.directory_tree_id", unreadable
    )

    assert run_main(monkeypatch, clone, "list") == 2
    assert "cannot read" in capsys.readouterr().err


def test_sync_reports_a_manifest_that_is_not_utf8_as_a_controlled_error(
    rule_package: tuple[pathlib.Path, str, str],
    history: History,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Invalid UTF-8 in the manifest exits 2 from `sync`, not with a traceback.

    The manifest's parameters are read, and refused with a controlled error,
    before `sync` reads the text to rewrite it, so the rewrite never sees the
    invalid bytes.
    """
    package, _root, _later = rule_package
    manifest = package / "rule.yaml"
    manifest.write_bytes(manifest.read_bytes() + b"\xff\xfe")
    clone = pathlib.Path(history.repository.workdir)

    assert run_main(monkeypatch, clone, "sync") == 2
    assert "cannot read rule manifest" in capsys.readouterr().err


def test_sync_reports_a_manifest_that_turns_invalid_between_reads(
    rule_package: tuple[pathlib.Path, str, str],
    history: History,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`sync` reads the manifest text again to rewrite it, and that read can fail.

    The parameters read first succeeds; the text read then meets invalid UTF-8,
    which exits 2 with an operational diagnostic and leaves the file as it was.
    """
    package, root, _later = rule_package
    manifest = package / "rule.yaml"
    manifest.write_bytes(manifest.read_bytes() + b"\xff\xfe")
    before = manifest.read_bytes()
    monkeypatch.setattr(
        whitaker_revisions, "_derive", lambda _c, _t: ([root], manifest)
    )
    clone = pathlib.Path(history.repository.workdir)

    assert run_main(monkeypatch, clone, "sync") == 2
    assert "cannot update" in capsys.readouterr().err
    assert manifest.read_bytes() == before


def test_check_accepts_a_reordered_manifest(
    rule_package: tuple[pathlib.Path, str, str],
    history: History,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`check` compares membership, so the same revisions in another order pass."""
    package, root, later = rule_package
    clone = pathlib.Path(history.repository.workdir)
    manifest = package / "rule.yaml"
    manifest.write_text(
        replace_refs(manifest.read_text("utf-8"), [later, root]), "utf-8"
    )
    assert packages.rule_parameters(package)[REFS_KEY] == [later, root]

    assert whitaker_revisions.check(clone, "main") == 0
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(("list", "{root}\n"), id="list"),
        pytest.param(("check", ""), id="check"),
        pytest.param(("sync", "{manifest}: 1 revisions\n"), id="sync"),
    ],
)
def test_the_commands_default_to_origin_main(
    rule_package: tuple[pathlib.Path, str, str],
    default_tip_cli: cabc.Callable[[str], int],
    capsys: pytest.CaptureFixture[str],
    case: tuple[str, str],
) -> None:
    """Without `--tip`, every command walks `origin/main`, not the local `main`.

    `origin/main` holds only the root while the local `main` also holds a later
    commit, so a command that used the local branch would list, report or write
    two revisions.
    """
    command, expected = case
    package, root, _later = rule_package

    assert default_tip_cli(command) == 0
    assert capsys.readouterr().out == expected.format(
        root=root, manifest=package / "rule.yaml"
    )


def test_sync_reports_how_many_revisions_it_wrote(
    rule_package: tuple[pathlib.Path, str, str],
    history: History,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The count the snapshot redacts is asserted here against the live output."""
    package, _root, _later = rule_package
    clone = pathlib.Path(history.repository.workdir)

    assert whitaker_revisions.sync(clone, "main") == 0
    assert capsys.readouterr().out == f"{package / 'rule.yaml'}: 2 revisions\n"
