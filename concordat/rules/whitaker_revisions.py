"""Derive the shared-actions revisions QG-002 accepts for `install-whitaker`.

The audited checkout holds no shared-actions history, so the rule cannot ask
whether a pinned revision still carries the install rules. It reads a list
instead, and this module is how the list is made honest. A revision is
listed when it is an approved root, or when it descends from one on the
default branch and leaves the action's directory content-identical to it,
compared by Git tree id. The tree id is a hash of the directory's contents,
so an identical id means the action a consumer runs is the approved one,
whatever else the commit changed elsewhere in shared-actions.

Only the history walk needs a clone; the functions here take an open
repository so tests can build a synthetic one.
"""

from __future__ import annotations

import re
import typing as typ

import pygit2

from concordat.errors import OperationalRuleError

if typ.TYPE_CHECKING:
    import collections.abc as cabc

OPERATION_DERIVE_REVISIONS: typ.Final = "derive-whitaker-revisions"
REFS_KEY: typ.Final = "compliant_install_whitaker_refs"
_FULL_SHA: typ.Final = re.compile(r"^[0-9a-f]{40}$")


def directory_tree_id(
    repository: pygit2.Repository, commit: pygit2.Commit, directory: str
) -> pygit2.Oid | None:
    """Return the tree id of *directory* at *commit*, or None when absent.

    Returns
    -------
    pygit2.Oid | None
        The directory's tree id, or None when the commit has no such
        directory.
    """
    try:
        entry = commit.tree[directory]
    except KeyError:
        return None
    return entry.id if repository[entry.id].type == pygit2.GIT_OBJECT_TREE else None


def _root_trees(
    repository: pygit2.Repository, roots: cabc.Sequence[str], directory: str
) -> dict[str, pygit2.Oid]:
    """Return each approved root's directory tree id.

    Returns
    -------
    dict[str, pygit2.Oid]
        The tree id of the action directory at each root.

    Raises
    ------
    OperationalRuleError
        When a root is not a commit in the clone or lacks the directory.
    """
    trees: dict[str, pygit2.Oid] = {}
    for root in roots:
        try:
            commit = repository[root]
        except (KeyError, ValueError) as error:
            message = f"approved root {root} is not in the shared-actions clone"
            raise OperationalRuleError(
                message, operation=OPERATION_DERIVE_REVISIONS
            ) from error
        tree = directory_tree_id(
            repository, typ.cast("pygit2.Commit", commit), directory
        )
        if tree is None:
            message = f"approved root {root} has no {directory} directory"
            raise OperationalRuleError(message, operation=OPERATION_DERIVE_REVISIONS)
        trees[root] = tree
    return trees


def compliant_revisions(
    repository: pygit2.Repository,
    roots: cabc.Sequence[str],
    directory: str,
    tip: str,
) -> list[str]:
    """Return every first-parent commit up to *tip* that carries an approved action.

    A commit qualifies when it is a root, or descends from a root and holds the
    action directory with that root's tree id. The list is oldest first, so an
    approved root leads it. A commit that changed the directory drops out until
    another root approves the new contents, which is what makes the rule refuse
    an unreviewed change to the action.

    Returns
    -------
    list[str]
        Full commit ids, oldest first.

    Raises
    ------
    OperationalRuleError
        When a root or *tip* cannot be resolved in the clone.
    """
    trees = _root_trees(repository, roots, directory)
    try:
        tip_id = typ.cast("pygit2.Commit", repository.revparse_single(tip)).id
    except (KeyError, ValueError) as error:
        message = f"{tip} does not name a commit in the shared-actions clone"
        raise OperationalRuleError(
            message, operation=OPERATION_DERIVE_REVISIONS
        ) from error
    walker = repository.walk(tip_id, pygit2.enums.SortMode.TOPOLOGICAL)
    walker.simplify_first_parent()
    found: list[str] = []
    for commit in walker:
        tree = directory_tree_id(repository, commit, directory)
        if any(
            tree == root_tree
            and (str(commit.id) == root or repository.descendant_of(commit.id, root))
            for root, root_tree in trees.items()
        ):
            found.append(str(commit.id))
    found.reverse()
    return found


def format_refs_block(refs: cabc.Sequence[str], indent: str = "      ") -> list[str]:
    """Return the YAML lines of a quoted revision list.

    Returns
    -------
    list[str]
        One `- "<sha>"` line per revision, indented for the defaults mapping.

    Raises
    ------
    OperationalRuleError
        When an entry is not a full commit id.
    """
    for ref in refs:
        if not _FULL_SHA.fullmatch(ref):
            message = f"{ref!r} is not a full lowercase commit id"
            raise OperationalRuleError(message, operation=OPERATION_DERIVE_REVISIONS)
    return [f'{indent}- "{ref}"' for ref in refs]


def replace_refs(rule_text: str, refs: cabc.Sequence[str]) -> str:
    """Return *rule_text* with its default revision list replaced by *refs*.

    Only the block at the `defaults` indentation is rewritten; the schema
    entry of the same key sits deeper and holds no list.

    Returns
    -------
    str
        The manifest text with the new list.

    Raises
    ------
    OperationalRuleError
        When the manifest has no default revision list to replace.
    """
    pattern = re.compile(
        rf'(^    {REFS_KEY}:\n)((?:      - "[0-9a-f]{{40}}"\n)+)', re.MULTILINE
    )
    if not pattern.search(rule_text):
        message = f"the rule manifest has no defaults.{REFS_KEY} list to replace"
        raise OperationalRuleError(message, operation=OPERATION_DERIVE_REVISIONS)
    block = "\n".join(format_refs_block(refs)) + "\n"
    return pattern.sub(lambda m: m.group(1) + block, rule_text, count=1)
