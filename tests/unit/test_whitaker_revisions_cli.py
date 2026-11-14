"""Specify the certification command line: `list`, `certify`, statuses, errors."""

from __future__ import annotations

import pathlib
import shlex
import sys
import typing as typ

import pygit2
import pytest

from scripts import whitaker_revisions
from tests.unit.whitaker_revisions_support import (
    ACTION_V1,
    ACTION_V2,
    DIRECTORY,
    History,
    action_files,
    run_main,
    tag,
)

pytest_plugins = ("tests.unit.whitaker_revisions_fixtures",)

PREFIX: typ.Final = "certified/install-whitaker/"


def _clone(history: History) -> pathlib.Path:
    """Return the path of the history's working directory."""
    return pathlib.Path(history.repository.workdir)


def test_list_prints_each_floor_then_what_it_certifies(
    history: History,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The documented `list` shows the tags and the revisions they certify.

    It goes through `main()` rather than calling the function: cyclopts names a
    command after its function, so only this route exercises the registered
    name. The commit that changes the runtime content is not certified.
    """
    root = history.commit(action_files(ACTION_V1), "root")
    later = history.commit(action_files(ACTION_V1, "b"), "readme only")
    history.commit(action_files(ACTION_V2), "changes the action")
    tag(history, f"{PREFIX}0.2.12", root)

    assert run_main(monkeypatch, _clone(history), "list") == 0

    assert capsys.readouterr().out.splitlines() == [
        f"floor {PREFIX}0.2.12 {root}",
        f"certified {root}",
        f"certified {later}",
    ]


@pytest.mark.parametrize("tag_name", [None, "v1.2.0"], ids=["no-tag", "release-tag"])
def test_list_prints_nothing_without_a_certification_tag(
    history: History,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tag_name: str | None,
) -> None:
    """With no tag, or only a release tag, nothing is certified."""
    root = history.commit(action_files(ACTION_V1), "root")
    if tag_name is not None:
        tag(history, tag_name, root)

    assert run_main(monkeypatch, _clone(history), "list") == 0
    assert capsys.readouterr().out == ""


def test_list_omits_a_descendant_that_adds_a_symlink_under_tests(
    history: History,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`list` agrees with the audit: a link anywhere makes a commit uncertifiable."""
    root = history.commit(action_files(ACTION_V1), "root")
    linked = {**action_files(ACTION_V1), f"{DIRECTORY}/tests/hook.sh": "../x\n"}
    history.commit(
        linked, "adds a link", symlinks=frozenset({f"{DIRECTORY}/tests/hook.sh"})
    )
    tag(history, f"{PREFIX}0.2.12", root)

    assert run_main(monkeypatch, _clone(history), "list") == 0
    assert capsys.readouterr().out.splitlines() == [
        f"floor {PREFIX}0.2.12 {root}",
        f"certified {root}",
    ]


def test_list_reads_a_lightweight_tag(
    history: History,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A tag made without a message still certifies its commit."""
    root = history.commit(action_files(ACTION_V1), "root")
    tag(history, f"{PREFIX}0.2.9", root, annotated=False)

    assert run_main(monkeypatch, _clone(history), "list") == 0
    assert f"certified {root}" in capsys.readouterr().out


def test_certify_prints_the_tag_commands_and_creates_nothing(
    history: History,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The maintainer runs the printed commands; the command tags nothing."""
    root = history.commit(action_files(ACTION_V1), "root")

    status = run_main(
        monkeypatch, _clone(history), "certify", root, "--installer-version", "0.2.12"
    )

    lines = capsys.readouterr().out.splitlines()
    assert status == 0
    assert lines[0].startswith("runtime digest ")
    git = f"git -C {shlex.quote(str(_clone(history)))}"
    assert lines[1:] == [
        f'{git} tag -a {PREFIX}0.2.12 {root} -m "Certify install-whitaker 0.2.12"',
        f"{git} push origin {PREFIX}0.2.12",
    ]
    assert not [r for r in history.repository.references if "certified" in r]


def test_certify_refuses_a_version_that_is_already_tagged(
    history: History,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A tag is never moved: a new version needs a new name."""
    root = history.commit(action_files(ACTION_V1), "root")
    later = history.commit(action_files(ACTION_V2), "next")
    tag(history, f"{PREFIX}0.2.12", root)

    status = run_main(
        monkeypatch, _clone(history), "certify", later, "--installer-version", "0.2.12"
    )

    assert status == 2
    assert "already has the tag" in capsys.readouterr().err


@pytest.mark.parametrize("version", ["latest", "0.2", "v0.2.12", ""])
def test_certify_refuses_a_malformed_version(
    history: History,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    version: str,
) -> None:
    """The tag name carries an installer version, so it must be one."""
    root = history.commit(action_files(ACTION_V1), "root")

    status = run_main(
        monkeypatch, _clone(history), "certify", root, "--installer-version", version
    )

    assert status == 2
    assert "is not a whitaker-installer version" in capsys.readouterr().err


def test_certify_refuses_a_commit_off_the_first_parent_line(
    history: History,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Only a commit on main can be a floor."""
    base = history.commit(action_files(ACTION_V1), "base")
    side = history.branch(action_files(ACTION_V1, "side"), "side branch", base)
    history.commit(action_files(ACTION_V1, "b"), "next on main")

    status = run_main(
        monkeypatch, _clone(history), "certify", side, "--installer-version", "0.2.12"
    )

    assert status == 2
    assert "first-parent history" in capsys.readouterr().err


def test_certify_refuses_a_commit_whose_runtime_names_excluded_content(
    history: History,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A script that runs content the digest ignores could change unseen."""
    root = history.commit(
        action_files("name: x\nrun: ./tests/helper.sh\n"), "reaches into tests"
    )

    status = run_main(
        monkeypatch, _clone(history), "certify", root, "--installer-version", "0.2.12"
    )

    assert status == 2
    assert "excludes" in capsys.readouterr().err


@pytest.mark.parametrize(
    "link", ["scripts/run.sh", "tests/helper.sh", "README.md"], ids=str
)
def test_certify_refuses_a_symlink_anywhere_in_the_action(
    history: History,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    link: str,
) -> None:
    """The digest hashes a link's text, not its target, even under `tests/`."""
    files = {
        **action_files(ACTION_V1),
        f"{DIRECTORY}/scripts/run.sh": "echo run\n",
        f"{DIRECTORY}/tests/helper.sh": "echo helper\n",
        f"{DIRECTORY}/README.md": "guide\n",
    }
    root = history.commit(files, "links", symlinks=frozenset({f"{DIRECTORY}/{link}"}))

    status = run_main(
        monkeypatch, _clone(history), "certify", root, "--installer-version", "0.2.12"
    )

    assert status == 2
    assert f"symlink or submodule at {link}" in capsys.readouterr().err


def test_certify_refuses_a_commit_the_clone_does_not_hold(
    history: History,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A commit from another clone is an error, not a traceback."""
    history.commit(action_files(ACTION_V1), "root")

    status = run_main(
        monkeypatch,
        _clone(history),
        "certify",
        "0" * 40,
        "--installer-version",
        "0.2.12",
    )

    assert status == 2
    assert "cannot read" in capsys.readouterr().err


def test_main_exits_2_for_a_clone_that_is_not_a_repository(
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The entry point turns an operational error into status 2."""
    empty = tmp_path_factory.mktemp("not-a-clone")
    monkeypatch.setattr(sys, "argv", ["whitaker_revisions", "list", str(empty)])

    assert whitaker_revisions.main() == 2
    assert "is not a Git repository" in capsys.readouterr().err


def test_a_git_read_failure_is_a_controlled_error_through_the_cli(
    history: History,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An object Git cannot read while walking history exits 2, not a traceback."""
    root = history.commit(action_files(ACTION_V1), "root")
    tag(history, f"{PREFIX}0.2.12", root)

    def unreadable(*_args: object) -> object:
        """Fail as Git does on a corrupt object."""
        message = "object not found"
        raise pygit2.GitError(message)

    monkeypatch.setattr("concordat.rules.whitaker_revisions.runtime_digest", unreadable)

    assert run_main(monkeypatch, _clone(history), "list") == 2
    assert "cannot read" in capsys.readouterr().err


def test_the_commands_default_to_origin_main(
    history: History,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """With no `--tip`, `list` walks `origin/main`, where a clone's main lives."""
    root = history.commit(action_files(ACTION_V1), "root")
    history.commit(action_files(ACTION_V1, "b"), "readme only")
    tag(history, f"{PREFIX}0.2.12", root)
    history.repository.references.create("refs/remotes/origin/main", root)
    monkeypatch.setattr(
        sys, "argv", ["whitaker_revisions", "list", str(_clone(history))]
    )

    assert whitaker_revisions.main() == 0
    assert capsys.readouterr().out.splitlines()[-1] == f"certified {root}"
