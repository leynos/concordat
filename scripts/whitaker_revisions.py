"""Keep QG-002's list of install-whitaker revisions honest.

Run against a shared-actions clone after shared-actions gains commits::

    uv run python scripts/whitaker_revisions.py sync --clone ../shared-actions

`sync` rewrites the rule manifest's `compliant_install_whitaker_refs` from the
approved roots; `check` exits 1 when the manifest differs from what the clone
derives; `list` prints the derived revisions. The derivation lives in
`concordat.rules.whitaker_revisions`.
"""

from __future__ import annotations

import sys
import typing as typ
from pathlib import Path  # noqa: TC003  # cyclopts reads the annotation at runtime.

import pygit2
from cyclopts import App

from concordat.errors import OperationalRuleError
from concordat.rules import packages
from concordat.rules.whitaker_revisions import (
    REFS_KEY,
    compliant_revisions,
    replace_refs,
)

RULE_ID: typ.Final = "whitaker-provisioning"
ROOTS_KEY: typ.Final = "install_whitaker_roots"

app = App(help=__doc__)


def _derive(clone: Path, tip: str) -> tuple[list[str], Path]:
    """Return the derived revisions and the rule manifest path.

    Returns
    -------
    tuple[list[str], Path]
        The revisions, oldest first, and the path of `rule.yaml`.

    Raises
    ------
    OperationalRuleError
        When the clone cannot be opened or the manifest names no roots.
    """
    rule_dir = packages.rule_package_dir(RULE_ID)
    parameters = packages.rule_parameters(rule_dir)
    roots = typ.cast("list[str]", parameters.get(ROOTS_KEY) or [])
    directory = typ.cast("str", parameters["action_directory"])
    if not roots:
        message = f"the rule manifest declares no {ROOTS_KEY}"
        raise OperationalRuleError(message, operation="derive-whitaker-revisions")
    try:
        repository = pygit2.Repository(str(clone))
    except pygit2.GitError as error:
        message = f"{clone} is not a Git repository: {error}"
        raise OperationalRuleError(
            message, operation="derive-whitaker-revisions", resource=clone
        ) from error
    return compliant_revisions(
        repository, roots, directory, tip
    ), rule_dir / "rule.yaml"


@app.command
def list_revisions(clone: Path, tip: str = "origin/main") -> int:
    """Print the revisions the clone derives, oldest first."""
    revisions, _ = _derive(clone, tip)
    print("\n".join(revisions))
    return 0


@app.command
def sync(clone: Path, tip: str = "origin/main") -> int:
    """Rewrite the rule manifest's revision list from the clone."""
    revisions, manifest = _derive(clone, tip)
    manifest.write_text(replace_refs(manifest.read_text("utf-8"), revisions), "utf-8")
    print(f"{manifest}: {len(revisions)} revisions")
    return 0


@app.command
def check(clone: Path, tip: str = "origin/main") -> int:
    """Exit 1 when the manifest's list differs from the clone's derivation."""
    revisions, manifest = _derive(clone, tip)
    listed = typ.cast(
        "list[str]",
        packages.rule_parameters(manifest.parent)[REFS_KEY],
    )
    missing = [rev for rev in revisions if rev not in listed]
    extra = [rev for rev in listed if rev not in revisions]
    for rev in missing:
        print(f"missing: {rev}")
    for rev in extra:
        print(f"not derivable: {rev}")
    return 1 if missing or extra else 0


def main() -> int:
    """Run the command line and return its exit status."""
    try:
        return typ.cast("int", app())
    except OperationalRuleError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
