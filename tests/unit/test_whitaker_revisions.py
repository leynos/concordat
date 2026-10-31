"""Specify how QG-002's list of install-whitaker revisions is derived.

Each scenario builds a small shared-actions history in a temporary repository,
because the derivation asks Git two questions (does the commit descend from an
approved root, and is the action's runtime digest the root's) and only a
real history answers both.
"""

from __future__ import annotations

import typing as typ

import pygit2
import pytest

from concordat.errors import OperationalRuleError
from concordat.rules.whitaker_revisions import (
    compliant_revisions,
)
from tests.unit.whitaker_revisions_support import (
    ACTION_V1,
    ACTION_V2,
    DIRECTORY,
    History,
    action_files,
)

pytest_plugins = ("tests.unit.whitaker_revisions_fixtures",)


def test_a_commit_that_leaves_the_directory_alone_is_listed(history: History) -> None:
    """Changes elsewhere in shared-actions do not disturb the approved action."""
    root = history.commit(action_files(ACTION_V1), "root")
    later = history.commit(action_files(ACTION_V1, "b"), "readme only")

    found = compliant_revisions(history.repository, [root], DIRECTORY, "main")

    assert found == [root, later]


def _runtime(action: str = ACTION_V1) -> dict[str, str]:
    """Return a small action directory: `action.yml`, one script, a test, a guide."""
    return {
        f"{DIRECTORY}/action.yml": action,
        f"{DIRECTORY}/scripts/install.sh": "echo install\n",
        f"{DIRECTORY}/installer-digests.sha256": "abc  installer\n",
        f"{DIRECTORY}/tests/test_action.py": "# v1\n",
        f"{DIRECTORY}/README.md": "guide v1\n",
    }


@pytest.mark.parametrize(
    "edit",
    [
        {f"{DIRECTORY}/tests/test_action.py": "# v2\n"},
        {f"{DIRECTORY}/tests/fixtures/new.json": "{}\n"},
        {f"{DIRECTORY}/README.md": "guide v2\n"},
        {f"{DIRECTORY}/CHANGELOG.md": "- entry\n"},
        {f"{DIRECTORY}/scripts/NOTES.md": "notes\n"},
    ],
    ids=["test-edit", "test-added", "readme", "changelog-added", "nested-markdown"],
)
def test_a_change_confined_to_tests_or_markdown_is_listed(
    history: History, edit: dict[str, str]
) -> None:
    """Tests and Markdown never run in a consumer's workflow, so they do not count."""
    root = history.commit(_runtime(), "root")
    later = history.commit({**_runtime(), **edit}, "tests or docs only")

    found = compliant_revisions(history.repository, [root], DIRECTORY, "main")

    assert found == [root, later]


@pytest.mark.parametrize(
    "edit",
    [
        {f"{DIRECTORY}/action.yml": ACTION_V2},
        {f"{DIRECTORY}/scripts/install.sh": "echo changed\n"},
        {f"{DIRECTORY}/installer-digests.sha256": "def  installer\n"},
        {f"{DIRECTORY}/snapshot-digests.sha256": "abc  snapshot\n"},
        {f"{DIRECTORY}/scripts/extra.sh": "echo extra\n"},
        {f"{DIRECTORY}/unknown.txt": "a new file at the action root\n"},
        {f"{DIRECTORY}/tests.sh": "echo a file whose name merely starts with tests\n"},
    ],
    ids=[
        "action-yml",
        "script-edit",
        "digest-manifest-edited",
        "digest-manifest-added",
        "script-added",
        "unknown-root-file",
        "tests-prefixed-name",
    ],
)
def test_a_change_to_runtime_content_is_refused(
    history: History, edit: dict[str, str]
) -> None:
    """Any runtime file that changes or appears drops the commit from the list."""
    root = history.commit(_runtime(), "root")
    changed = history.commit({**_runtime(), **edit}, "changes runtime content")

    found = compliant_revisions(history.repository, [root], DIRECTORY, "main")

    assert found == [root]
    assert changed not in found


def test_a_changed_file_mode_is_refused(history: History) -> None:
    """Making a helper script executable changes what the action runs."""
    root = history.commit(_runtime(), "root")
    changed = history.commit(
        _runtime(),
        "makes the script executable",
        executable=frozenset({f"{DIRECTORY}/scripts/install.sh"}),
    )

    found = compliant_revisions(history.repository, [root], DIRECTORY, "main")

    assert found == [root]
    assert changed not in found


def test_a_renamed_runtime_file_is_refused(history: History) -> None:
    """The path is part of the digest, so a move is a change."""
    root = history.commit(_runtime(), "root")
    files = _runtime()
    files[f"{DIRECTORY}/scripts/setup.sh"] = files.pop(
        f"{DIRECTORY}/scripts/install.sh"
    )
    renamed = history.commit(files, "renames the script")

    found = compliant_revisions(history.repository, [root], DIRECTORY, "main")

    assert renamed not in found


@pytest.mark.parametrize(
    "reference",
    ["bash tests/helper.sh\n", "cat README.md\n", "cat docs/guide.md | sh\n"],
    ids=["tests-path", "readme", "markdown-path"],
)
def test_a_root_whose_runtime_names_an_excluded_path_is_refused(
    history: History, reference: str
) -> None:
    """A script that runs or reads an excluded file would escape the digest."""
    files = {**_runtime(), f"{DIRECTORY}/scripts/install.sh": reference}
    root = history.commit(files, "root that leans on an excluded path")

    with pytest.raises(OperationalRuleError, match="excludes"):
        compliant_revisions(history.repository, [root], DIRECTORY, "main")


def test_a_commit_before_the_root_is_refused(history: History) -> None:
    """A revision that predates the root lacks the install rules, however alike."""
    older = history.commit(action_files(ACTION_V1), "same directory, before the root")
    root = history.commit(action_files(ACTION_V1, "b"), "root")

    found = compliant_revisions(history.repository, [root], DIRECTORY, "main")

    assert older not in found
    assert found == [root]


def test_a_commit_that_changes_the_directory_is_refused(history: History) -> None:
    """An unreviewed change to the action drops out until a root approves it."""
    root = history.commit(action_files(ACTION_V1), "root")
    changed = history.commit(action_files(ACTION_V2), "changes the action")
    after = history.commit(action_files(ACTION_V2, "b"), "keeps the change")

    found = compliant_revisions(history.repository, [root], DIRECTORY, "main")

    assert found == [root]
    assert changed not in found
    assert after not in found


def test_a_second_root_approves_the_new_contents(history: History) -> None:
    """Approving the changed action lists it and what follows unchanged."""
    root = history.commit(action_files(ACTION_V1), "root")
    history.commit(action_files(ACTION_V2), "changes the action")
    second = history.commit(action_files(ACTION_V2, "b"), "approved root")
    after = history.commit(action_files(ACTION_V2, "c"), "unchanged after")

    found = compliant_revisions(history.repository, [root, second], DIRECTORY, "main")

    assert found == [root, second, after]


def test_reverting_to_the_approved_contents_is_listed_again(history: History) -> None:
    """The test is content, not commit lineage of the directory."""
    root = history.commit(action_files(ACTION_V1), "root")
    history.commit(action_files(ACTION_V2), "changes the action")
    back = history.commit(action_files(ACTION_V1), "reverts it")

    found = compliant_revisions(history.repository, [root], DIRECTORY, "main")

    assert found == [root, back]


def test_only_the_first_parent_line_is_listed(history: History) -> None:
    """A merged-in side commit is not on main's line, even with approved contents.

    The merge itself is listed because main's own tree holds the approved
    directory. The side commit that kept the directory is excluded because
    nothing on main's line reviewed it, and the one that changed it is excluded
    either way, so a pin cannot land on a commit main never reviewed.
    """
    root = history.commit(action_files(ACTION_V1), "root")
    side_same = history.branch(
        action_files(ACTION_V1, "side"), "side, same action", root
    )
    side_changed = history.branch(
        action_files(ACTION_V2), "side, changed action", side_same
    )
    merged = history.merge(action_files(ACTION_V1, "merged"), "merge", side_changed)

    found = compliant_revisions(history.repository, [root], DIRECTORY, "main")

    assert found == [root, merged]
    assert side_same not in found
    assert side_changed not in found


def test_a_missing_directory_is_refused(history: History) -> None:
    """A commit without the action cannot carry its rules."""
    root = history.commit(action_files(ACTION_V1), "root")
    history.commit({"README.md": "no action"}, "removes the directory")

    found = compliant_revisions(history.repository, [root], DIRECTORY, "main")

    assert found == [root]


@pytest.mark.parametrize(
    "roots", [["0" * 40], ["not-a-commit"]], ids=["unknown-id", "not-an-id"]
)
def test_an_unknown_root_is_an_operational_error(
    history: History, roots: list[str]
) -> None:
    """A root the clone does not hold means the clone is the wrong one."""
    history.commit(action_files(ACTION_V1), "root")

    with pytest.raises(OperationalRuleError, match="approved root"):
        compliant_revisions(history.repository, roots, DIRECTORY, "main")


def test_a_root_without_the_action_directory_is_an_operational_error(
    history: History,
) -> None:
    """An approved root that never held the action cannot anchor the list."""
    root = history.commit({"README.md": "a"}, "root without the action")

    with pytest.raises(OperationalRuleError, match=r"has no \S+ directory"):
        compliant_revisions(history.repository, [root], DIRECTORY, "main")


def test_a_root_off_the_first_parent_line_is_an_operational_error(
    history: History,
) -> None:
    """A root the walk never reaches would silently vanish from the list."""
    base = history.commit(action_files(ACTION_V1), "base")
    side = history.branch(action_files(ACTION_V1, "side"), "side branch", base)
    history.commit(action_files(ACTION_V1, "b"), "next on main")

    with pytest.raises(OperationalRuleError, match="first-parent history"):
        compliant_revisions(history.repository, [side], DIRECTORY, "main")


def test_a_shallow_clone_is_an_operational_error(
    history: History, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A shallow walk would drop older qualifying commits without a word."""
    root = history.commit(action_files(ACTION_V1), "root")
    monkeypatch.setattr(
        type(history.repository), "is_shallow", property(lambda _self: True)
    )

    with pytest.raises(OperationalRuleError, match="shallow"):
        compliant_revisions(history.repository, [root], DIRECTORY, "main")


@pytest.mark.parametrize(
    "failure",
    [KeyError("tip"), pygit2.GitError("unreadable")],
    ids=["unknown-ref", "git-error"],
)
def test_an_unresolvable_tip_is_an_operational_error(
    history: History, monkeypatch: pytest.MonkeyPatch, failure: Exception
) -> None:
    """A tip Git cannot resolve is reported as a controlled error."""
    root = history.commit(action_files(ACTION_V1), "root")

    def refuse(_self: object, _tip: str) -> typ.NoReturn:
        raise failure

    monkeypatch.setattr(type(history.repository), "revparse_single", refuse)

    with pytest.raises(OperationalRuleError, match="readable commit"):
        compliant_revisions(history.repository, [root], DIRECTORY, "main")
