"""Contract tests for the CodeScene uploader's deprecated checksum inputs.

At ``a5765019`` the shared uploader's committed ``cli-manifest.json`` is the
trust anchor for the CLI archive, and the action *rejects* a non-empty
``installer-checksum`` with a hard failure rather than ignoring it. A workflow
that still passes the input therefore breaks the upload step the moment the
pin moves, and a repository variable feeding it can only ever repeat the
manifest digest.

Four separate concerns are asserted, each in its own test so that a failure
names the defect rather than a bundle:

* no workflow passes ``installer-checksum``;
* no workflow references the ``CODESCENE_CLI_SHA256`` variable that fed it;
* every uploader reference is pinned to one approved full SHA;
* the dispatch workflow that refreshed the variable is gone.

Each test asserts over a non-empty set. A contract that ranges over an empty
collection is satisfied by deleting the thing it guards, so the collections
are checked for content before they are checked for compliance.
"""

from __future__ import annotations

import re
import typing as typ
from pathlib import Path

REPOSITORY_ROOT: typ.Final = Path(__file__).parents[2]
WORKFLOW_DIRECTORY: typ.Final = REPOSITORY_ROOT / ".github/workflows"

_UPLOADER_PIN: typ.Final = "a5765019912a8ab6882b12db049c7cde635f3a85"
_UPLOADER_REFERENCE: typ.Final = re.compile(
    r"leynos/shared-actions/\.github/actions/upload-codescene-coverage@(\S+)"
)
_DEPRECATED_INPUT: typ.Final = "installer-checksum"
_DEPRECATED_VARIABLE: typ.Final = "CODESCENE_CLI_SHA256"
_REFRESH_WORKFLOW: typ.Final = "get-codescene-sha.yml"


def _workflow_files() -> tuple[Path, ...]:
    """Return this repository's own workflow files, sorted for stable failures.

    Only ``.github/workflows`` is read. Canon data under ``platform-standards``
    ships workflow documents that describe other repositories' standards, and
    they are not this repository's CI.

    Returns
    -------
        Every ``.yml`` and ``.yaml`` file in this repository's workflow
        directory, in sorted order.
    """
    workflows = tuple(
        sorted(
            path
            for pattern in ("*.yml", "*.yaml")
            for path in WORKFLOW_DIRECTORY.glob(pattern)
        )
    )
    assert workflows, (
        "no workflow files were found, so every workflow contract below would "
        "pass vacuously"
    )
    return workflows


def test_no_workflow_passes_the_deprecated_installer_checksum() -> None:
    """The uploader rejects a non-empty value, so no workflow may pass it."""
    offenders = [
        path.relative_to(REPOSITORY_ROOT).as_posix()
        for path in _workflow_files()
        if _DEPRECATED_INPUT in path.read_text(encoding="utf-8")
    ]
    assert not offenders, (
        f"{_DEPRECATED_INPUT} is deprecated and rejected by the uploader at "
        f"{_UPLOADER_PIN}; remove it from {', '.join(offenders)}"
    )


def test_no_workflow_references_the_deprecated_checksum_variable() -> None:
    """The variable existed only to feed the rejected input, so it must go."""
    offenders = [
        path.relative_to(REPOSITORY_ROOT).as_posix()
        for path in _workflow_files()
        if _DEPRECATED_VARIABLE in path.read_text(encoding="utf-8")
    ]
    assert not offenders, (
        f"{_DEPRECATED_VARIABLE} fed the deprecated installer checksum and has "
        f"no remaining consumer; remove it from {', '.join(offenders)}"
    )


def test_every_uploader_reference_is_pinned_to_the_approved_sha() -> None:
    """One approved SHA, asserted as an allowlist rather than as a floor.

    A floor would require ordering SHAs, which cannot be computed from a
    checkout. Naming the approved pin keeps the contract hermetic and fails
    closed on any other value, including a tag or a branch name.
    """
    references = {
        path.relative_to(REPOSITORY_ROOT).as_posix(): match.group(1)
        for path in _workflow_files()
        for match in _UPLOADER_REFERENCE.finditer(path.read_text(encoding="utf-8"))
    }
    assert references, (
        "no uploader reference was found, so the pin assertion below would pass "
        "vacuously; this repository is expected to upload coverage from main"
    )
    wrong = {path: pin for path, pin in references.items() if pin != _UPLOADER_PIN}
    assert not wrong, (
        f"every upload-codescene-coverage reference must be pinned to "
        f"{_UPLOADER_PIN}; found {wrong}"
    )


def test_the_checksum_refresh_workflow_is_absent() -> None:
    """Nothing consumes the variable it wrote, so the workflow is dead code."""
    refresh = WORKFLOW_DIRECTORY / _REFRESH_WORKFLOW
    assert not refresh.exists(), (
        f"{_REFRESH_WORKFLOW} refreshed {_DEPRECATED_VARIABLE}, which no "
        "workflow reads any more; delete it rather than leaving a dispatch "
        "that writes an unused repository variable"
    )
