"""Specify how QG-002's list of install-whitaker revisions is derived.

Each scenario builds a small shared-actions history in a temporary repository,
because the derivation asks Git two questions (does the commit descend from an
approved root, and is the action directory's tree id the root's) and only a
real history answers both.
"""

from __future__ import annotations

import os
import pathlib
import re
import sys
import typing as typ

import pygit2
import pytest

from concordat.errors import OperationalRuleError
from concordat.rules import packages
from concordat.rules.whitaker_revisions import (
    REFS_KEY,
    compliant_revisions,
    replace_refs,
)
from scripts import whitaker_revisions

DIRECTORY: typ.Final = ".github/actions/install-whitaker"
ACTION_V1: typ.Final = "name: install-whitaker\nversion: 1\n"
ACTION_V2: typ.Final = "name: install-whitaker\nversion: 2\n"
SIGNATURE: typ.Final = pygit2.Signature(
    "Test", "test@example.invalid", 1_700_000_000, 0
)


class History:
    """Build a linear shared-actions history one commit at a time."""

    def __init__(self, path: pathlib.Path) -> None:
        """Initialise an empty repository at *path*."""
        self.repository = pygit2.init_repository(str(path), initial_head="main")
        self.parents: list[pygit2.Oid] = []

    def commit(self, files: dict[str, str], message: str) -> str:
        """Commit *files* as the whole tree and return the new commit id."""
        tree = self._tree(files)
        oid = self.repository.create_commit(
            "refs/heads/main", SIGNATURE, SIGNATURE, message, tree, self.parents
        )
        self.parents = [oid]
        return str(oid)

    def branch(self, files: dict[str, str], message: str, parent: str) -> str:
        """Commit *files* on a side branch off *parent* without moving main."""
        oid = self.repository.create_commit(
            None,
            SIGNATURE,
            SIGNATURE,
            message,
            self._tree(files),
            [pygit2.Oid(hex=parent)],
        )
        return str(oid)

    def merge(self, files: dict[str, str], message: str, second: str) -> str:
        """Commit a merge of *second* into main, with main as first parent."""
        oid = self.repository.create_commit(
            "refs/heads/main",
            SIGNATURE,
            SIGNATURE,
            message,
            self._tree(files),
            [*self.parents, pygit2.Oid(hex=second)],
        )
        self.parents = [oid]
        return str(oid)

    def _tree(self, files: dict[str, str]) -> pygit2.Oid:
        """Write a nested tree for *files* and return its id."""
        builder = self.repository.TreeBuilder()
        nested: dict[str, dict[str, str]] = {}
        for name, text in files.items():
            head, _, tail = name.partition("/")
            if tail:
                nested.setdefault(head, {})[tail] = text
            else:
                blob = self.repository.create_blob(text.encode())
                builder.insert(name, blob, pygit2.GIT_FILEMODE_BLOB)
        for head, children in nested.items():
            builder.insert(head, self._tree(children), pygit2.GIT_FILEMODE_TREE)
        return builder.write()


def _files(action: str, other: str = "a") -> dict[str, str]:
    """Return a tree holding *action* in the directory and *other* elsewhere."""
    return {f"{DIRECTORY}/action.yml": action, "README.md": other}


@pytest.fixture
def history(tmp_path: pathlib.Path) -> History:
    """Return an empty synthetic shared-actions history."""
    return History(tmp_path)


def test_a_commit_that_leaves_the_directory_alone_is_listed(history: History) -> None:
    """Changes elsewhere in shared-actions do not disturb the approved action."""
    root = history.commit(_files(ACTION_V1), "root")
    later = history.commit(_files(ACTION_V1, "b"), "readme only")

    found = compliant_revisions(history.repository, [root], DIRECTORY, "main")

    assert found == [root, later]


def test_a_commit_before_the_root_is_refused(history: History) -> None:
    """A revision that predates the root lacks the install rules, however alike."""
    older = history.commit(_files(ACTION_V1), "same directory, before the root")
    root = history.commit(_files(ACTION_V1, "b"), "root")

    found = compliant_revisions(history.repository, [root], DIRECTORY, "main")

    assert older not in found
    assert found == [root]


def test_a_commit_that_changes_the_directory_is_refused(history: History) -> None:
    """An unreviewed change to the action drops out until a root approves it."""
    root = history.commit(_files(ACTION_V1), "root")
    changed = history.commit(_files(ACTION_V2), "changes the action")
    after = history.commit(_files(ACTION_V2, "b"), "keeps the change")

    found = compliant_revisions(history.repository, [root], DIRECTORY, "main")

    assert found == [root]
    assert changed not in found
    assert after not in found


def test_a_second_root_approves_the_new_contents(history: History) -> None:
    """Approving the changed action lists it and what follows unchanged."""
    root = history.commit(_files(ACTION_V1), "root")
    history.commit(_files(ACTION_V2), "changes the action")
    second = history.commit(_files(ACTION_V2, "b"), "approved root")
    after = history.commit(_files(ACTION_V2, "c"), "unchanged after")

    found = compliant_revisions(history.repository, [root, second], DIRECTORY, "main")

    assert found == [root, second, after]


def test_reverting_to_the_approved_contents_is_listed_again(history: History) -> None:
    """The test is content, not commit lineage of the directory."""
    root = history.commit(_files(ACTION_V1), "root")
    history.commit(_files(ACTION_V2), "changes the action")
    back = history.commit(_files(ACTION_V1), "reverts it")

    found = compliant_revisions(history.repository, [root], DIRECTORY, "main")

    assert found == [root, back]


def test_only_the_first_parent_line_is_listed(history: History) -> None:
    """A merged-in side commit is not on main's line, even with approved contents.

    The merge itself is listed because main's own tree holds the approved
    directory. The side commit that kept the directory is excluded because
    nothing on main's line reviewed it, and the one that changed it is excluded
    either way, so a pin cannot land on a commit main never reviewed.
    """
    root = history.commit(_files(ACTION_V1), "root")
    side_same = history.branch(_files(ACTION_V1, "side"), "side, same action", root)
    side_changed = history.branch(_files(ACTION_V2), "side, changed action", side_same)
    merged = history.merge(_files(ACTION_V1, "merged"), "merge", side_changed)

    found = compliant_revisions(history.repository, [root], DIRECTORY, "main")

    assert found == [root, merged]
    assert side_same not in found
    assert side_changed not in found


def test_a_missing_directory_is_refused(history: History) -> None:
    """A commit without the action cannot carry its rules."""
    root = history.commit(_files(ACTION_V1), "root")
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
    history.commit(_files(ACTION_V1), "root")

    with pytest.raises(OperationalRuleError, match="approved root"):
        compliant_revisions(history.repository, roots, DIRECTORY, "main")


def test_a_shallow_clone_is_an_operational_error(
    history: History, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A shallow walk would drop older qualifying commits without a word."""
    root = history.commit(_files(ACTION_V1), "root")
    monkeypatch.setattr(
        type(history.repository), "is_shallow", property(lambda _self: True)
    )

    with pytest.raises(OperationalRuleError, match="shallow"):
        compliant_revisions(history.repository, [root], DIRECTORY, "main")


def test_replacing_the_list_touches_only_the_defaults() -> None:
    """The schema entry of the same key is prose and keeps its wording."""
    text = (
        "    properties:\n      " + REFS_KEY + ":\n        type: array\n"
        "  defaults:\n    " + REFS_KEY + ':\n      - "' + "a" * 40 + '"\n    other: 1\n'
    )

    updated = replace_refs(text, ["b" * 40, "c" * 40])

    assert updated == (
        text.split("  defaults:")[0]
        + "  defaults:\n    "
        + REFS_KEY
        + ':\n      - "'
        + "b" * 40
        + '"\n      - "'
        + "c" * 40
        + '"\n    other: 1\n'
    )


def test_replacing_a_list_that_is_absent_is_an_error() -> None:
    """A manifest with no list is not silently left unchanged."""
    with pytest.raises(OperationalRuleError, match="no defaults"):
        replace_refs("defaults: {}\n", ["a" * 40])


def test_replacing_refuses_a_short_revision() -> None:
    """Only full commit ids may enter the list the policy compares against."""
    text = f'    {REFS_KEY}:\n      - "{"a" * 40}"\n'

    with pytest.raises(OperationalRuleError, match="full lowercase"):
        replace_refs(text, ["abc123"])


def test_check_reports_revisions_the_clone_does_not_derive(
    history: History,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`check` fails, naming both directions, when the manifest drifts."""
    root = history.commit(_files(ACTION_V1), "root")
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


def test_the_shipped_list_leads_with_its_roots() -> None:
    """Every approved root is listed, and the first entry is the first root."""
    parameters = packages.rule_parameters(
        packages.rule_package_dir("whitaker-provisioning")
    )
    roots = typ.cast("list[str]", parameters["install_whitaker_roots"])
    refs = typ.cast("list[str]", parameters[REFS_KEY])

    assert refs[0] == roots[0]
    assert set(roots) <= set(refs)
    assert len(refs) == len(set(refs))


def _rule_copy(tmp_path: pathlib.Path, root: str) -> pathlib.Path:
    """Copy the shipped rule manifest with *root* as its only approved root."""
    real = packages.rule_package_dir("whitaker-provisioning")
    package = tmp_path / "package"
    package.mkdir()
    text = (real / "rule.yaml").read_text("utf-8")
    text = re.sub(
        r'(install_whitaker_roots:\n)((?:      - "[0-9a-f]{40}"\n)+)',
        lambda m: m.group(1) + f'      - "{root}"\n',
        text,
        count=1,
    )
    text = replace_refs(text, [root])
    (package / "rule.yaml").write_text(text, "utf-8")
    return package


@pytest.fixture
def rule_package(
    tmp_path: pathlib.Path, history: History, monkeypatch: pytest.MonkeyPatch
) -> tuple[pathlib.Path, str, str]:
    """Point the command at a copy of the rule manifest rooted in *history*."""
    root = history.commit(_files(ACTION_V1), "root")
    later = history.commit(_files(ACTION_V1, "b"), "readme only")
    package = _rule_copy(tmp_path, root)
    monkeypatch.setattr(packages, "rule_package_dir", lambda _rule_id: package)
    return package, root, later


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
