"""Build the evidence the Whitaker provisioning policy evaluates.

The estate provisions Whitaker one way only: the shared-actions
`install-whitaker` action. The policy therefore needs to see every place a
checkout could provision it by another route, which is wider than the
workflows: a composite action, a Makefile recipe, or a script that CI runs can
each download or build the installer just as well.

This builder gathers those surfaces and nothing else. Workflows and composite
action manifests are decoded, because the policy reads their `uses:` pins and
`run:` bodies structurally. Makefiles and scripts are carried as text, because
the policy only asks whether they name an install or a download of the
Whitaker tools, and a shell or Make parser would add a failure mode without
answering that question any better. A surface that cannot be read stays in the
envelope with its reason, so the policy can report it as indeterminate rather
than treat it as absent.

The repository's GitHub slug, read from the `origin` remote, is recorded so the
policy can match the named exemptions its parameters declare. It is `None`
when the checkout has no GitHub origin, and then no exemption can match.
"""

from __future__ import annotations

import os
import pathlib
import stat
import typing as typ

import pygit2

from concordat.platform_standards import parse_github_slug

from .codescene_coverage_envelope import (
    Repository,
    WorkflowFile,
    _load_workflow,
    _load_workflows,
)

ENVELOPE_SCHEMA_VERSION: typ.Final = 1
ENVELOPE_KIND: typ.Final = "policy-input/whitaker-provisioning"

#: Directories that hold dependencies, build output or tool caches rather than
#: the repository's own automation. Walking them would only slow the audit and
#: report vendored code the repository does not run.
SKIPPED_DIRECTORIES: typ.Final = frozenset({
    ".git",
    ".hg",
    ".tox",
    ".uv-cache",
    ".venv",
    "__pycache__",
    "node_modules",
    "target",
    "venv",
})

ACTION_MANIFEST_NAMES: typ.Final = frozenset({"action.yml", "action.yaml"})
MAKEFILE_NAMES: typ.Final = frozenset({"Makefile", "GNUmakefile", "makefile"})
MAKEFILE_SUFFIX: typ.Final = ".mk"

#: Top-level directories whose scripts CI conventionally runs.
SCRIPT_DIRECTORIES: typ.Final = frozenset({".github", "bin", "ci", "scripts", "tools"})
SCRIPT_SUFFIXES: typ.Final = frozenset({"", ".bash", ".ps1", ".py", ".sh", ".zsh"})

#: A script larger than this is recorded as unreadable rather than loaded.
MAX_SCRIPT_BYTES: typ.Final = 1024 * 1024

#: How much of a file is checked for a NUL byte to tell a binary from text.
BINARY_SNIFF_BYTES: typ.Final = 8192

#: Directory names that hold test code, which is not automation CI runs.
TEST_DIRECTORIES: typ.Final = frozenset({"test", "tests", "__tests__", "fixtures"})


class TextFile(typ.TypedDict):
    """Store one Makefile or script as text, or the reason it is unreadable.

    Attributes
    ----------
    path:
        The file's path relative to the checkout, in POSIX form.
    text:
        The file's UTF-8 content, or None when it could not be read.
    error:
        Why the file could not be read, or None when it was.
    """

    path: str
    text: str | None
    error: str | None


class ProvisioningEnvelope(typ.TypedDict):
    """Store the evidence evaluated by the Whitaker provisioning rule.

    Attributes
    ----------
    schema_version:
        The envelope's schema version.
    kind:
        The policy-input identifier, checked by the policy.
    repository:
        The checkout's path and, where the origin remote names one, its
        GitHub `owner/name` slug.
    workflows:
        Every root workflow document, decoded or carrying its error.
    actions:
        Every composite action manifest in the checkout, decoded or carrying
        its error.
    scripts:
        Every Makefile, and every script under a conventional automation
        directory or at the root, as text or carrying its error.
    """

    schema_version: int
    kind: str
    repository: Repository
    workflows: list[WorkflowFile]
    actions: list[WorkflowFile]
    scripts: list[TextFile]


def _origin_slug(checkout: pathlib.Path) -> str | None:
    """Return the checkout's GitHub slug from its origin remote, if any.

    Returns
    -------
    str | None
        The `owner/name` slug, or None when there is no repository, no
        origin, or an origin that is not a GitHub URL.
    """
    discovered = pygit2.discover_repository(str(checkout))
    if discovered is None:
        return None
    try:
        origin = pygit2.Repository(discovered).remotes["origin"]
    except (KeyError, pygit2.GitError):
        return None
    return parse_github_slug(origin.url) if origin.url else None


def _walk(checkout: pathlib.Path) -> list[pathlib.PurePosixPath]:
    """Return every non-skipped file path under the checkout, sorted.

    Symbolic links to directories are not followed, so a link cannot make
    the audit read another tree as this one.

    Returns
    -------
    list[pathlib.PurePosixPath]
        Checkout-relative file paths in POSIX form.
    """
    found: list[pathlib.PurePosixPath] = []
    for root, directories, files in os.walk(checkout, followlinks=False):
        directories[:] = sorted(d for d in directories if d not in SKIPPED_DIRECTORIES)
        relative_root = pathlib.Path(root).relative_to(checkout)
        found.extend(
            pathlib.PurePosixPath(relative_root.as_posix()) / name for name in files
        )
    return sorted(found)


def _is_action_manifest(relative: pathlib.PurePosixPath) -> bool:
    """Return whether *relative* is a composite action manifest."""
    return relative.name in ACTION_MANIFEST_NAMES and relative.parts[:2] != (
        ".github",
        "workflows",
    )


def _is_makefile(relative: pathlib.PurePosixPath) -> bool:
    """Return whether *relative* is a Makefile or a Make include."""
    return relative.name in MAKEFILE_NAMES or relative.suffix == MAKEFILE_SUFFIX


def _has_shebang(path: pathlib.Path) -> bool:
    """Return whether *path* starts with `#!`, treating a read failure as no."""
    try:
        with path.open("rb") as handle:
            return handle.read(2) == b"#!"
    except OSError:
        return False


def _is_test(relative: pathlib.PurePosixPath) -> bool:
    """Return whether *relative* is test code rather than automation.

    A test names the tools it exercises and stubs the downloads it guards
    against, so reading it as a script would report the test of a rule as a
    breach of it. CI runs tests; it does not provision anything through them.

    Returns
    -------
    bool
        True for a file in a test directory or named as a test module.
    """
    name = relative.name
    return (
        any(part in TEST_DIRECTORIES for part in relative.parts[:-1])
        or name.startswith("test_")
        or relative.stem.endswith("_test")
    )


def _is_script(checkout: pathlib.Path, relative: pathlib.PurePosixPath) -> bool:
    """Return whether *relative* is a script CI could run.

    A script lives under a conventional automation directory with a script
    suffix, or sits at the root with a shebang. Workflow and action YAML is
    read structurally instead, and test code is not automation, so both are
    excluded here.

    Returns
    -------
    bool
        True for a file the envelope carries as a script.
    """
    if relative.suffix in {".yml", ".yaml"} or _is_test(relative):
        return False
    if len(relative.parts) == 1:
        return _has_shebang(checkout / relative)
    return (
        relative.parts[0] in SCRIPT_DIRECTORIES and relative.suffix in SCRIPT_SUFFIXES
    )


def _contained_target(
    checkout: pathlib.Path, path: pathlib.Path
) -> pathlib.Path | None:
    """Return a symlink's target when it resolves inside the checkout.

    A link within the repository, such as `bin/tool-x -> tool`, is the
    repository's own content under a second name, and is read as such. A link
    that leaves the checkout, or cannot be resolved, is not this repository's
    to report on.

    Returns
    -------
    pathlib.Path | None
        The resolved target, or None when it is outside the checkout.
    """
    try:
        target = path.resolve(strict=True)
        root = checkout.resolve(strict=True)
    except OSError:
        return None
    return target if target.is_relative_to(root) and target.is_file() else None


def _read_script(path: pathlib.Path, fact: TextFile) -> TextFile | None:
    """Fill *fact* from *path*, or return None when the file is binary.

    A file with a NUL byte near its start, or one that is not UTF-8, is a
    compiled artefact rather than a script, such as a tool kept under `bin/`.
    No shell reads it as text, and recording it as unreadable would make the
    repository indeterminate for a reason unrelated to provisioning. Only a
    text file too large to load is recorded as unreadable.

    Returns
    -------
    TextFile | None
        The completed fact, or None for a binary file.
    """
    try:
        with path.open("rb") as handle:
            content = handle.read(MAX_SCRIPT_BYTES + 1)
    except OSError as error:
        fact["error"] = f"cannot read: {error}"
        return fact
    if b"\0" in content[:BINARY_SNIFF_BYTES]:
        return None
    if len(content) > MAX_SCRIPT_BYTES:
        fact["error"] = f"larger than {MAX_SCRIPT_BYTES} bytes"
        return fact
    try:
        fact["text"] = content.decode("utf-8")
    except UnicodeDecodeError:
        return None
    return fact


def _load_text(
    checkout: pathlib.Path, relative: pathlib.PurePosixPath
) -> TextFile | None:
    """Return one text fact, retaining any read failure as its reason.

    Returns
    -------
    TextFile | None
        The file's content or the reason it could not be read, or None for a
        binary file.
    """
    fact: TextFile = {"path": str(relative), "text": None, "error": None}
    path = checkout / relative
    try:
        status = path.lstat()
    except OSError as error:
        fact["error"] = f"cannot stat: {error}"
        return fact
    if stat.S_ISLNK(status.st_mode):
        target = _contained_target(checkout, path)
        if target is None:
            fact["error"] = "symlink that leaves the checkout"
            return fact
        return _read_script(target, fact)
    if not stat.S_ISREG(status.st_mode):
        fact["error"] = "not a regular file"
        return fact
    return _read_script(path, fact)


def _load_action(
    checkout: pathlib.Path, relative: pathlib.PurePosixPath
) -> WorkflowFile:
    """Return one decoded action manifest, retaining any content failure.

    Returns
    -------
    WorkflowFile
        The decoded manifest, or the reason it could not be decoded.
    """
    is_link = (checkout / relative).is_symlink()
    return _load_workflow(checkout, relative, is_link=is_link)


def build_whitaker_provisioning_envelope(
    checkout: pathlib.Path,
) -> ProvisioningEnvelope:
    """Assemble the Whitaker provisioning evidence for one checkout.

    Parameters
    ----------
    checkout:
        The repository checkout to read.

    An `OperationalRuleError` reaches the caller when the workflow directory
    resolves outside the checkout or cannot be listed; every other surface
    that cannot be read is recorded as a fact with its reason.

    Returns
    -------
    ProvisioningEnvelope
        The decoded workflows and actions, and the Makefiles and scripts.
    """
    paths = _walk(checkout)
    return {
        "schema_version": ENVELOPE_SCHEMA_VERSION,
        "kind": ENVELOPE_KIND,
        "repository": {"path": str(checkout), "name": _origin_slug(checkout)},
        "workflows": _load_workflows(checkout),
        "actions": [
            _load_action(checkout, path) for path in paths if _is_action_manifest(path)
        ],
        "scripts": [
            fact
            for path in paths
            if _is_makefile(path) or _is_script(checkout, path)
            if (fact := _load_text(checkout, path)) is not None
        ],
    }
