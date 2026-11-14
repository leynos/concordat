"""List what the certification tags of `install-whitaker` certify, from a clone.

QG-002 certifies a shared-actions revision for `install-whitaker` when it
descends from a certification tag (a *floor*) and leaves the action's *runtime
content* identical to that floor's. The audit decides that through the GitHub
API (see `concordat.rules.whitaker_certification`); this module is the local
form over a clone, which lets a reviewer list what the tags certify and check a
candidate floor before tagging it.

Runtime content is every file in the action's directory except the `tests/`
tree and Markdown files. Those are never executed by a consumer's workflow,
so a change confined to them leaves the action a consumer runs untouched;
everything else (`action.yml`, the helper scripts, the digest manifests, and
any file added later) counts as runtime, so an unknown file fails closed. The
runtime digest hashes each file's mode, blob id and path, so a content, mode,
rename or addition change moves it. A floor whose runtime files point at an
excluded path would let an unreviewed edit through, so such a floor is refused.

Only the history walk needs a clone; the functions here take an open
repository so tests can build a synthetic one.
"""

from __future__ import annotations

import re
import typing as typ

import pygit2

from concordat.errors import OperationalRuleError

from .whitaker_certification import is_excluded, runtime_digest_of, unsafe_entry

if typ.TYPE_CHECKING:
    import collections.abc as cabc

OPERATION_DERIVE_REVISIONS: typ.Final = "derive-whitaker-revisions"
_EXCLUDED_DIRECTORY: typ.Final = "tests"
# A runtime file naming an excluded path (a test helper, a Markdown file) could
# execute or read content the digest does not cover.
_EXCLUDED_REFERENCE: typ.Final = re.compile(rb"tests/|\.md\b")


def _runtime_entries(
    repository: pygit2.Repository, tree: pygit2.Tree, prefix: str = ""
) -> cabc.Iterator[tuple[str, int, pygit2.Oid]]:
    """Yield `(path, filemode, blob id)` for every runtime file below *tree*.

    Yields
    ------
    tuple[str, int, pygit2.Oid]
        A runtime file's path relative to the action directory, its Git file
        mode and its blob id.
    """
    for entry in tree:
        relative = f"{prefix}{entry.name}"
        if entry.filemode == pygit2.GIT_FILEMODE_TREE:
            if relative != _EXCLUDED_DIRECTORY:
                yield from _runtime_entries(
                    repository,
                    typ.cast("pygit2.Tree", repository[entry.id]),
                    f"{relative}/",
                )
        elif not is_excluded(relative):
            yield relative, entry.filemode, entry.id


def _action_tree(
    repository: pygit2.Repository, commit: pygit2.Commit, directory: str
) -> pygit2.Tree | None:
    """Return the tree of *directory* at *commit*, or None when absent."""
    try:
        entry = commit.tree[directory]
    except KeyError:
        return None
    node = repository[entry.id]
    return (
        typ.cast("pygit2.Tree", node) if node.type == pygit2.GIT_OBJECT_TREE else None
    )


def runtime_digest(
    repository: pygit2.Repository, commit: pygit2.Commit, directory: str
) -> str | None:
    """Return the runtime digest of *directory* at *commit*, or None when absent.

    The digest is the SHA-256 of the sorted `<mode> <blob id> <path>` records
    of the directory's runtime files (see the module documentation). Blob ids
    hash content, and the read writes nothing to the clone.

    Returns
    -------
    str | None
        A hex digest, or None when the commit has no such directory or its
        directory holds a symlink or submodule (which the audit never certifies,
        so the listing must not either).
    """
    tree = _action_tree(repository, commit, directory)
    if tree is None or unsafe_entry(_all_entries(repository, tree)) is not None:
        return None
    return runtime_digest_of(
        (path, f"{mode:o}", str(blob))
        for path, mode, blob in _runtime_entries(repository, tree)
    )


def _refuse_excluded_references(
    repository: pygit2.Repository, commit: pygit2.Commit, directory: str, root: str
) -> None:
    """Refuse a root whose runtime files name a path the digest ignores.

    Raises
    ------
    OperationalRuleError
        When a runtime file of *root* mentions `tests/` or a Markdown file.
    """
    tree = _action_tree(repository, commit, directory)
    if tree is None:
        return
    for path, _mode, blob in _runtime_entries(repository, tree):
        content = typ.cast("pygit2.Blob", repository[blob]).data
        if _EXCLUDED_REFERENCE.search(content):
            message = (
                f"approved root {root} runtime file {path} names a path the "
                "runtime digest excludes (tests/ or a Markdown file); review it "
                "before approving"
            )
            raise OperationalRuleError(message, operation=OPERATION_DERIVE_REVISIONS)


def _all_entries(
    repository: pygit2.Repository, tree: pygit2.Tree, prefix: str = ""
) -> cabc.Iterator[tuple[str, str, str]]:
    """Yield `(path, octal mode, id)` for every non-tree entry, excluded ones too.

    Yields
    ------
    tuple[str, str, str]
        A path relative to the action directory, its mode and its object id.
    """
    for entry in tree:
        relative = f"{prefix}{entry.name}"
        if entry.filemode == pygit2.GIT_FILEMODE_TREE:
            subtree = typ.cast("pygit2.Tree", repository[entry.id])
            yield from _all_entries(repository, subtree, f"{relative}/")
        else:
            yield relative, f"{entry.filemode:o}", str(entry.id)


def _refuse_links(
    repository: pygit2.Repository, commit: pygit2.Commit, directory: str, root: str
) -> None:
    """Refuse a root with a symlink or a submodule anywhere in the directory.

    The digest hashes a link's text or a gitlink's commit, not what they lead
    to, and ignores `tests/` and Markdown wholesale, so a link could change what
    the action runs without moving the digest.

    Raises
    ------
    OperationalRuleError
        When the directory holds a symlink or a submodule.
    """
    tree = _action_tree(repository, commit, directory)
    link = None if tree is None else unsafe_entry(_all_entries(repository, tree))
    if link is not None:
        message = (
            f"approved root {root} has a symlink or submodule at {link} in "
            f"{directory}, whose target the runtime digest does not cover"
        )
        raise OperationalRuleError(message, operation=OPERATION_DERIVE_REVISIONS)


def floor_digest(repository: pygit2.Repository, root: str, directory: str) -> str:
    """Return the action directory's runtime digest at one approved root.

    Returns
    -------
    str
        The runtime digest of the action directory at *root*.

    Raises
    ------
    OperationalRuleError
        When *root* is not a readable commit in the clone, lacks the
        directory, has a runtime file that names an excluded path, or holds a
        symlink or a submodule.
    """
    try:
        commit = typ.cast("pygit2.Commit", repository[root])
        digest = runtime_digest(repository, commit, directory)
        _refuse_excluded_references(repository, commit, directory, root)
        _refuse_links(repository, commit, directory, root)
    except (pygit2.GitError, KeyError, ValueError) as error:
        message = f"cannot read approved root {root} from the shared-actions clone"
        raise OperationalRuleError(
            message, operation=OPERATION_DERIVE_REVISIONS
        ) from error
    if digest is None:
        message = f"approved root {root} has no {directory} directory"
        raise OperationalRuleError(message, operation=OPERATION_DERIVE_REVISIONS)
    return digest


def _root_digests(
    repository: pygit2.Repository, roots: cabc.Sequence[str], directory: str
) -> dict[str, str]:
    """Return each approved root's runtime digest.

    Returns
    -------
    dict[str, str]
        The runtime digest of the action directory at each root;
        `floor_digest`'s operational error propagates for a root it cannot
        read.
    """
    return {root: floor_digest(repository, root, directory) for root in roots}


def _is_root_or_descendant(
    repository: pygit2.Repository, commit: pygit2.Commit, root: str
) -> bool:
    """Report whether *commit* is *root* or descends from it."""
    return str(commit.id) == root or repository.descendant_of(commit.id, root)


def _carries_approved_action(
    repository: pygit2.Repository,
    commit: pygit2.Commit,
    digest: str | None,
    digests: cabc.Mapping[str, str],
) -> bool:
    """Report whether *commit* holds an approved root's runtime content.

    The digest must equal the root's and the commit must be that root or
    descend from it, so neither an older commit nor a changed action passes.

    Returns
    -------
    bool
        True when an approved root accepts the commit.
    """
    return any(
        digest == root_digest and _is_root_or_descendant(repository, commit, root)
        for root, root_digest in digests.items()
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
    action directory with that root's runtime digest. The list is oldest first, so an
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
    digests = _root_digests(repository, roots, directory)
    tip_id = _resolve_tip(repository, tip)
    found = _walk_or_refuse(repository, tip_id, directory, digests)
    _require_every_root(found, roots, tip)
    return found


def _walk_or_refuse(
    repository: pygit2.Repository,
    tip_id: pygit2.Oid,
    directory: str,
    digests: cabc.Mapping[str, str],
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
        return _walk_first_parent(repository, tip_id, directory, digests)
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
    digests: cabc.Mapping[str, str],
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
        digest = runtime_digest(repository, commit, directory)
        if _carries_approved_action(repository, commit, digest, digests):
            found.append(str(commit.id))
    found.reverse()
    return found


def certified_tags(repository: pygit2.Repository, prefix: str) -> dict[str, str]:
    """Return the commit each certification tag in *repository* points at.

    A tag is a floor when its name starts with *prefix* (for example
    `certified/install-whitaker/`). An annotated tag is peeled to its commit.

    Returns
    -------
    dict[str, str]
        Tag name to full commit id, sorted by tag name.

    Raises
    ------
    OperationalRuleError
        When a matching tag does not peel to a commit.
    """
    floors: dict[str, str] = {}
    for reference in repository.references:
        if not reference.startswith(f"refs/tags/{prefix}"):
            continue
        try:
            target = repository.references[reference].peel(pygit2.Commit)
        except (pygit2.GitError, KeyError, ValueError) as error:
            message = f"certification tag {reference} does not name a commit"
            raise OperationalRuleError(
                message, operation=OPERATION_DERIVE_REVISIONS
            ) from error
        floors[reference.removeprefix("refs/tags/")] = str(target.id)
    return dict(sorted(floors.items()))


def certified_revisions(
    repository: pygit2.Repository,
    prefix: str,
    directory: str,
    tip: str,
) -> list[str]:
    """Return every first-parent commit up to *tip* that some tag certifies.

    This is the local-clone form of the rule's certification: a commit is
    certified when it is a tagged floor, or descends from one and holds that
    floor's runtime digest. The audit applies the same test through the GitHub
    API; this form lets a reviewer list what the tags certify.

    Returns
    -------
    list[str]
        Full commit ids, oldest first. Empty when no tag matches.
    """
    floors = sorted(set(certified_tags(repository, prefix).values()))
    if not floors:
        return []
    return compliant_revisions(repository, floors, directory, tip)
