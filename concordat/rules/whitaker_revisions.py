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


def _root_tree(repository: pygit2.Repository, root: str, directory: str) -> pygit2.Oid:
    """Return the action directory's tree id at one approved root.

    Returns
    -------
    pygit2.Oid
        The tree id of the action directory at *root*.

    Raises
    ------
    OperationalRuleError
        When *root* is not a readable commit in the clone or lacks the
        directory.
    """
    try:
        commit = typ.cast("pygit2.Commit", repository[root])
        tree = directory_tree_id(repository, commit, directory)
    except (pygit2.GitError, KeyError, ValueError) as error:
        message = f"cannot read approved root {root} from the shared-actions clone"
        raise OperationalRuleError(
            message, operation=OPERATION_DERIVE_REVISIONS
        ) from error
    if tree is None:
        message = f"approved root {root} has no {directory} directory"
        raise OperationalRuleError(message, operation=OPERATION_DERIVE_REVISIONS)
    return tree


def _root_trees(
    repository: pygit2.Repository, roots: cabc.Sequence[str], directory: str
) -> dict[str, pygit2.Oid]:
    """Return each approved root's directory tree id.

    Returns
    -------
    dict[str, pygit2.Oid]
        The tree id of the action directory at each root; `_root_tree`'s
        operational error propagates for a root it cannot read.
    """
    return {root: _root_tree(repository, root, directory) for root in roots}


def _is_root_or_descendant(
    repository: pygit2.Repository, commit: pygit2.Commit, root: str
) -> bool:
    """Report whether *commit* is *root* or descends from it."""
    return str(commit.id) == root or repository.descendant_of(commit.id, root)


def _carries_approved_action(
    repository: pygit2.Repository,
    commit: pygit2.Commit,
    tree: pygit2.Oid | None,
    trees: cabc.Mapping[str, pygit2.Oid],
) -> bool:
    """Report whether *commit* holds an approved root's action directory.

    The tree id must equal the root's and the commit must be that root or
    descend from it, so neither an older commit nor a changed directory passes.

    Returns
    -------
    bool
        True when an approved root accepts the commit.
    """
    return any(
        tree == root_tree and _is_root_or_descendant(repository, commit, root)
        for root, root_tree in trees.items()
    )


def _resolve_tip(repository: pygit2.Repository, tip: str) -> pygit2.Oid:
    """Return the commit id that *tip* names in the clone.

    Returns
    -------
    pygit2.Oid
        The id of the commit at *tip*.

    Raises
    ------
    OperationalRuleError
        When *tip* does not resolve to a commit or Git cannot read it.
    """
    try:
        return typ.cast("pygit2.Commit", repository.revparse_single(tip)).id
    except (pygit2.GitError, KeyError, ValueError) as error:
        message = f"{tip} does not name a readable commit in the shared-actions clone"
        raise OperationalRuleError(
            message, operation=OPERATION_DERIVE_REVISIONS
        ) from error


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

    A shallow clone ends the first-parent walk at its boundary, so the list
    derived from it would silently omit older qualifying commits.

    Returns
    -------
    list[str]
        Full commit ids, oldest first.

    Raises
    ------
    OperationalRuleError
        When the clone is shallow, a root or *tip* cannot be resolved in it,
        an approved root is not on the first-parent history of *tip*, or Git
        cannot read an object while walking the history.
    """
    if repository.is_shallow:
        message = "the shared-actions clone is shallow; run git fetch --unshallow"
        raise OperationalRuleError(message, operation=OPERATION_DERIVE_REVISIONS)
    trees = _root_trees(repository, roots, directory)
    tip_id = _resolve_tip(repository, tip)
    found = _walk_or_refuse(repository, tip_id, directory, trees)
    _require_every_root(found, roots, tip)
    return found


def _walk_or_refuse(
    repository: pygit2.Repository,
    tip_id: pygit2.Oid,
    directory: str,
    trees: cabc.Mapping[str, pygit2.Oid],
) -> list[str]:
    """Return the first-parent walk, translating Git read failures.

    Returns
    -------
    list[str]
        Full commit ids, oldest first.

    Raises
    ------
    OperationalRuleError
        When Git cannot read an object while walking the history.
    """
    try:
        return _walk_first_parent(repository, tip_id, directory, trees)
    except (pygit2.GitError, KeyError, ValueError) as error:
        message = f"cannot read the shared-actions history: {error}"
        raise OperationalRuleError(
            message, operation=OPERATION_DERIVE_REVISIONS
        ) from error


def _require_every_root(
    found: cabc.Sequence[str], roots: cabc.Sequence[str], tip: str
) -> None:
    """Refuse a derivation that leaves out an approved root.

    Raises
    ------
    OperationalRuleError
        When an approved root is not on the first-parent history of *tip*.
    """
    absent = [root for root in roots if root not in found]
    if absent:
        message = (
            f"approved root {absent[0]} is not on the first-parent history of {tip}"
        )
        raise OperationalRuleError(message, operation=OPERATION_DERIVE_REVISIONS)


def _walk_first_parent(
    repository: pygit2.Repository,
    tip_id: pygit2.Oid,
    directory: str,
    trees: cabc.Mapping[str, pygit2.Oid],
) -> list[str]:
    """Return the qualifying first-parent commits up to *tip_id*, oldest first.

    Returns
    -------
    list[str]
        Full commit ids of the commits an approved root accepts.
    """
    walker = repository.walk(tip_id, pygit2.enums.SortMode.TOPOLOGICAL)
    walker.simplify_first_parent()
    found: list[str] = []
    for commit in walker:
        tree = directory_tree_id(repository, commit, directory)
        if _carries_approved_action(repository, commit, tree, trees):
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
