"""Regenerate the policy-input fixture envelopes for dependency-family-contracts.

Each scenario lays out a synthetic checkout from the file contents written
below, then hands it to the production ``build_dependency_family_envelope`` so
the recorded envelopes are exactly what ``concordat artefact rule run`` would
send to Conftest. ``data.json`` bundles every envelope under a ``fixtures`` key
for ``conftest verify``.

Run from the repository root::

    uv run python \
        platform-standards/canon/lint-rules/dependency-family-contracts/fixtures/generate.py
"""

from __future__ import annotations

import json
import tempfile
import typing as typ
from pathlib import Path

from concordat.rules.dependency_family_envelope import (
    DependencyFamilyEnvelope,
    build_dependency_family_envelope,
)

FIXTURES_DIR = Path(__file__).resolve().parent
DATA_PATH = FIXTURES_DIR / "data.json"

# The recorded repository path must not leak the temporary directory the
# scenario was laid out in; every envelope names the checkout as `.`.
RECORDED_REPOSITORY_PATH: typ.Final = "."

CARGO = """\
[package]
name = "demo"
version = "0.1.0"
edition = "2024"

[dependencies]
pyo3 = "0.28.3"
pyo3-build-config = "0.28.3"
"""
LOCK = """\
version = 4

[[package]]
name = "pyo3"
version = "0.28.3"

[[package]]
name = "pyo3-build-config"
version = "0.28.3"
"""
NARROW_FAMILY = """\
      pyo3:
        patterns: ['pyo3', 'pyo3-build-config']
"""
BRIDGE_PUBLISHED = """\
      pyo3:
        patterns: ['pyo3', 'pyo3-build-config', 'serde-pyobject']
"""
BRIDGE_ALIAS = """\
      pyo3:
        patterns: ['pyo3', 'pyo3-build-config', 'pyo3-serde']
"""
CATCH_ALL = """\
      minor-and-patch:
        patterns: ['*']
        update-types: [minor, patch]
"""


def dependabot(
    groups: str, *, ecosystem: str = "cargo", directory: str = "/", extra: str = ""
) -> str:
    """Return a Dependabot configuration with one entry and the given groups."""
    return (
        "version: 2\nupdates:\n"
        f"  - package-ecosystem: {ecosystem}\n    directory: {directory}\n"
        f"    schedule:\n      interval: daily\n    groups:\n{groups}{extra}"
    )


def define_multi(config: str, definition: str) -> str:
    """Return *config* with the top-level multi-ecosystem groups *definition*."""
    return config.replace("version: 2\n", "version: 2\n" + definition, 1)


def entries_of(config: str) -> str:
    """Return the `updates` entries of *config*, without its header."""
    return config.split("updates:\n", 1)[1]


def two_entry_config(group_entries: str, plain_entries: str) -> str:
    """Return a config listing the group entries before the plain ones."""
    return define_multi(
        "version: 2\nupdates:\n" + group_entries + entries_of(plain_entries),
        MULTI_DEFINITION,
    )


def maturin_with_multi(
    definition: str, cargo_join: str, maturin_join: str
) -> dict[str, str]:
    """Return a maturin and PyO3 checkout whose entries join the given groups."""
    config = (
        dependabot(NARROW_FAMILY + CATCH_ALL, extra=cargo_join)
        + PIP_ENTRY
        + CATCH_ALL
        + maturin_join
    )
    return {
        **base(),
        "pyproject.toml": MATURIN_PYPROJECT.format(spec="==1.13.3"),
        ".github/dependabot.yml": define_multi(config, definition)
        if definition
        else config,
    }


def base(
    groups: str = NARROW_FAMILY + CATCH_ALL, *, cargo: str = CARGO, lock: str = LOCK
) -> dict[str, str]:
    """Return a Rust checkout whose PyO3 family is covered by *groups*."""
    return {
        "Cargo.toml": cargo,
        "Cargo.lock": lock,
        ".github/dependabot.yml": dependabot(groups),
    }


def with_files(files: dict[str, str], **overrides: str | None) -> dict[str, str]:
    """Return *files* with the named paths replaced, or removed when None."""
    merged = {**files}
    for path, text in overrides.items():
        key = path.replace("__", "/").replace("_dot_", ".")
        if text is None:
            merged.pop(key, None)
        else:
            merged[key] = text
    return merged


SERDE_LOCK = LOCK + '\n[[package]]\nname = "serde-pyobject"\nversion = "0.8.0"\n'
SERDE_CARGO = CARGO + 'pyo3-serde = { package = "serde-pyobject", version = "0.8.0" }\n'
MATURIN_PYPROJECT = """\
[project]
name = "demo"
version = "0.1.0"
dependencies = ["maturin{spec}"]
"""
NESTED_WORKSPACE = """\
[workspace]
members = ["native"]

[workspace.dependencies]
pyo3 = "0.28.3"
pyo3-build-config = "0.28.3"
"""
NESTED_MEMBER = """\
[package]
name = "native"
version = "0.1.0"
edition = "2024"

[dependencies]
pyo3 = { workspace = true }

[build-dependencies]
pyo3-build-config = { workspace = true }
"""
NETSUKE_CARGO = """\
[package]
name = "demo"
version = "0.1.0"
edition = "2024"

[dependencies]
ortho_config = "0.9.0"

[dev-dependencies]
fluent-bundle = "0.16.0"
"""
NETSUKE_LOCK = """\
version = 4

[[package]]
name = "ortho_config"
version = "0.9.0"

[[package]]
name = "fluent-bundle"
version = "0.16.0"
"""
FLUENT_GROUP = """\
      fluent:
        patterns: ['ortho_config', 'fluent-bundle']
"""

SHADOWING = """\
      pyo3-only:
        patterns: ['pyo3']
      family:
        patterns: ['pyo3', 'pyo3-build-config']
"""

ROOT_WORKSPACE = """\
[workspace]
members = ["native"]

[workspace.dependencies]
pyo3 = "0.28.3"
pyo3-build-config = "0.28.3"
"""

SURFACES = (
    "language:\n  rust:\n    surfaces:\n"
    "      - path: rust/Cargo.toml\n      - path: rust/native/Cargo.toml\n"
)
PIP_ENTRY = (
    "  - package-ecosystem: pip\n    directory: /\n"
    "    schedule:\n      interval: daily\n    groups:\n"
)
MULTI_DEFINITION = (
    "multi-ecosystem-groups:\n  maturin-pyo3:\n    schedule:\n      interval: daily\n"
)
OTHER_DEFINITION = (
    MULTI_DEFINITION + "  other-group:\n    schedule:\n      interval: daily\n"
)
CARGO_GROUP_ENTRY = (
    "  - package-ecosystem: cargo\n    directory: /\n"
    "    multi-ecosystem-group: maturin-pyo3\n"
    "    patterns: ['pyo3', 'pyo3-build-config']\n"
)
CARGO_GROUP_ENTRY_PYO3_ONLY = (
    "  - package-ecosystem: cargo\n    directory: /\n"
    "    multi-ecosystem-group: maturin-pyo3\n    patterns: ['pyo3']\n"
)
IGNORE_FAMILY = (
    "    ignore:\n      - dependency-name: pyo3\n"
    "      - dependency-name: pyo3-build-config\n"
)
IGNORE_MATURIN = "    ignore:\n      - dependency-name: maturin\n"
PIP_GROUP_ENTRY = (
    "  - package-ecosystem: pip\n    directory: /\n"
    "    multi-ecosystem-group: maturin-pyo3\n    patterns: ['maturin']\n"
)
JOIN_PYO3 = "    multi-ecosystem-group: maturin-pyo3\n    patterns: ['pyo3']\n"
JOIN_MATURIN = "    multi-ecosystem-group: maturin-pyo3\n    patterns: ['maturin']\n"
JOIN_MATURIN_ELSEWHERE = (
    "    multi-ecosystem-group: other-group\n    patterns: ['maturin']\n"
)
JOIN_MATURIN_MISSING = (
    "    multi-ecosystem-group: maturin-pyo3\n    patterns: ['pytest']\n"
)
SERDE_IGNORE = (
    "    ignore:\n      - dependency-name: {name}\n        versions: ['>=0.9']\n"
)
SERDE_A = (
    '[package]\nname = "a"\nversion = "0.1.0"\n[dependencies]\nserde = "1.0.100"\n'
)
SERDE_B = (
    '[package]\nname = "b"\nversion = "0.1.0"\n[dependencies]\nserde = "1.0.200"\n'
)
EXCEPTION = (
    "dependencies:\n  family_exceptions:\n"
    "    - relationship: pyo3-build-config-family\n"
    "      rationale: the extension stays on the 0.28 generation\n"
    "      review_trigger: review when PyO3 0.30 ships\n"
)
INCOMPLETE_EXCEPTION = (
    "dependencies:\n  family_exceptions:\n"
    "    - relationship: pyo3-build-config-family\n"
    "      rationale: the extension stays on the 0.28 generation\n"
)

SCENARIOS: dict[str, dict[str, str]] = {
    # Both PyO3 packages in one narrow group ahead of the catch-all.
    "compliant_family": base(),
    # No narrow group: both fall to the catch-all.
    "split_family": base(CATCH_ALL),
    # A narrow group matches both, but an earlier group takes pyo3 first.
    "shadowed_family": base(SHADOWING + CATCH_ALL),
    # An optional member and a target-specific one still count as declared.
    "optional_member": base(
        CATCH_ALL,
        cargo=CARGO.replace(
            'pyo3-build-config = "0.28.3"',
            'pyo3-build-config = { version = "0.28.3", optional = true }',
        ),
    ),
    # A nested workspace member reaches Dependabot through its covered root.
    "nested_manifest_split": {
        ".concordat": SURFACES,
        "rust/Cargo.toml": NESTED_WORKSPACE,
        "rust/native/Cargo.toml": NESTED_MEMBER,
        "rust/Cargo.lock": LOCK,
        ".github/dependabot.yml": dependabot(CATCH_ALL, directory="/rust"),
    },
    "nested_manifest_grouped": {
        ".concordat": SURFACES,
        "rust/Cargo.toml": NESTED_WORKSPACE,
        "rust/native/Cargo.toml": NESTED_MEMBER,
        "rust/Cargo.lock": LOCK,
        ".github/dependabot.yml": dependabot(
            NARROW_FAMILY + CATCH_ALL, directory="/rust"
        ),
    },
    # A member of the root workspace is read through it, undeclared: the split
    # is seen in the member, and the grouped variant is clean.
    "workspace_member_split": {
        "Cargo.toml": ROOT_WORKSPACE,
        "native/Cargo.toml": NESTED_MEMBER,
        "Cargo.lock": LOCK,
        ".github/dependabot.yml": dependabot(CATCH_ALL),
    },
    "workspace_member_grouped": {
        "Cargo.toml": ROOT_WORKSPACE,
        "native/Cargo.toml": NESTED_MEMBER,
        "Cargo.lock": LOCK,
        ".github/dependabot.yml": dependabot(NARROW_FAMILY + CATCH_ALL),
    },
    # A renamed bridge: Dependabot matches the published name, not the alias.
    "renamed_bridge_unprotected": base(
        NARROW_FAMILY + CATCH_ALL, cargo=SERDE_CARGO, lock=SERDE_LOCK
    ),
    "renamed_bridge_grouped_by_published_name": base(
        BRIDGE_PUBLISHED + CATCH_ALL, cargo=SERDE_CARGO, lock=SERDE_LOCK
    ),
    "renamed_bridge_grouped_by_alias": base(
        BRIDGE_ALIAS + CATCH_ALL, cargo=SERDE_CARGO, lock=SERDE_LOCK
    ),
    # maturin (Python) and PyO3 (Rust): an exact pin is not protection.
    "maturin_exact_pin": {
        **base(),
        "pyproject.toml": MATURIN_PYPROJECT.format(spec="==1.13.3"),
        ".github/dependabot.yml": dependabot(NARROW_FAMILY + CATCH_ALL)
        + PIP_ENTRY
        + CATCH_ALL,
    },
    # An ignore rule on a member of an independently versioned tuple protects it.
    "maturin_pyo3_ignored": {
        **base(),
        "pyproject.toml": MATURIN_PYPROJECT.format(spec="==1.13.3"),
        ".github/dependabot.yml": dependabot(
            NARROW_FAMILY + CATCH_ALL, extra=SERDE_IGNORE.format(name="pyo3")
        )
        + PIP_ENTRY
        + CATCH_ALL,
    },
    "maturin_bounded": {
        **base(),
        "pyproject.toml": MATURIN_PYPROJECT.format(spec=">=1.13.3,<2.0.0"),
        ".github/dependabot.yml": dependabot(NARROW_FAMILY + CATCH_ALL)
        + PIP_ENTRY
        + CATCH_ALL,
    },
    # One multi-ecosystem group takes maturin and PyO3 into a single pull
    # request, so one CI run sees the pair: protected without a bound.
    "maturin_multi_group": maturin_with_multi(
        MULTI_DEFINITION, JOIN_PYO3, JOIN_MATURIN
    ),
    # Only PyO3's entry joins: maturin still arrives alone.
    "maturin_multi_group_one_member": maturin_with_multi(
        MULTI_DEFINITION, JOIN_PYO3, ""
    ),
    # The members join different groups, so they arrive in different pull requests.
    "maturin_multi_group_split": maturin_with_multi(
        OTHER_DEFINITION, JOIN_PYO3, JOIN_MATURIN_ELSEWHERE
    ),
    # The entries name a group the file never defines: Dependabot rejects it.
    "maturin_multi_group_undefined": maturin_with_multi("", JOIN_PYO3, JOIN_MATURIN),
    # The group is defined and joined, but its patterns do not take maturin.
    "maturin_multi_group_patterns_miss": maturin_with_multi(
        MULTI_DEFINITION, JOIN_PYO3, JOIN_MATURIN_MISSING
    ),
    # The shape Dependabot needs: an entry that joins a group runs only that
    # group, so each ecosystem also keeps a plain entry (ignoring the member)
    # for everything else. The group entry comes first; the plain entry supplies
    # the groups for the rest. The family and the tuple are both protected.
    "maturin_two_entries": {
        **base(),
        "pyproject.toml": MATURIN_PYPROJECT.format(spec="==1.13.3"),
        ".github/dependabot.yml": two_entry_config(
            CARGO_GROUP_ENTRY + PIP_GROUP_ENTRY,
            dependabot(NARROW_FAMILY + CATCH_ALL, extra=IGNORE_FAMILY)
            + PIP_ENTRY
            + CATCH_ALL
            + IGNORE_MATURIN,
        ),
    },
    # The merged cuprum shape: a lone group entry leaves the rest of the
    # ecosystem unhandled, so a family that needs a narrow group reads as split.
    "family_lone_group_entry": {
        **base(),
        ".github/dependabot.yml": define_multi(
            "version: 2\nupdates:\n" + CARGO_GROUP_ENTRY_PYO3_ONLY,
            MULTI_DEFINITION,
        ),
    },
    # A pure-Python repository with a copied wheel template: PyO3 is absent, so
    # the maturin relationship does not apply and no lockstep is invented.
    "pure_python_template": {
        "pyproject.toml": MATURIN_PYPROJECT.format(spec="==1.13.3"),
        ".github/dependabot.yml": dependabot(CATCH_ALL, ecosystem="pip"),
    },
    # Type identity: ortho_config and fluent-bundle in one group.
    "fluent_grouped": {
        "Cargo.toml": NETSUKE_CARGO,
        "Cargo.lock": NETSUKE_LOCK,
        ".github/dependabot.yml": dependabot(FLUENT_GROUP + CATCH_ALL),
    },
    "fluent_split": {
        "Cargo.toml": NETSUKE_CARGO,
        "Cargo.lock": NETSUKE_LOCK,
        ".github/dependabot.yml": dependabot(CATCH_ALL),
    },
    "fluent_without_lock": {
        "Cargo.toml": NETSUKE_CARGO,
        ".github/dependabot.yml": dependabot(FLUENT_GROUP + CATCH_ALL),
    },
    # Unrelated duplicate crate versions are not a defect.
    "unrelated_duplicates": {
        "Cargo.toml": SERDE_A,
        "native/Cargo.toml": SERDE_B,
        ".concordat": (
            "language:\n  rust:\n    surfaces:\n"
            "      - path: Cargo.toml\n      - path: native/Cargo.toml\n"
        ),
        "Cargo.lock": 'version = 4\n[[package]]\nname = "serde"\nversion = "1.0.200"\n',
        ".github/dependabot.yml": dependabot(CATCH_ALL),
    },
    # An undeclared manifest that names no related package cannot hide a
    # relationship, so it draws no finding.
    "undeclared_unrelated_manifest": {
        "tools/Cargo.toml": SERDE_A,
        ".github/dependabot.yml": dependabot(CATCH_ALL),
    },
    # An undeclared manifest that cannot be decoded may hide one: fail closed.
    "undeclared_unparsable_manifest": {
        "tools/Cargo.toml": "[package\nname = ",
        ".github/dependabot.yml": dependabot(CATCH_ALL),
    },
    # A Cargo manifest no declared surface governs is not read, and says so.
    "undeclared_manifest": {
        "rust/Cargo.toml": CARGO,
        "rust/Cargo.lock": LOCK,
        ".github/dependabot.yml": dependabot(NARROW_FAMILY + CATCH_ALL),
    },
    # An intentionally older generation recorded as a reviewed exception.
    "split_with_exception": {
        **base(CATCH_ALL),
        ".concordat": EXCEPTION,
    },
    # An exception without a review trigger is a problem, not an exemption.
    "split_with_incomplete_exception": {
        **base(CATCH_ALL),
        ".concordat": INCOMPLETE_EXCEPTION,
    },
    # No Dependabot configuration: nothing can be established.
    "no_dependabot": {"Cargo.toml": CARGO, "Cargo.lock": LOCK},
    # A Rust manifest that cannot be parsed.
    "unparsable_manifest": {
        "Cargo.toml": "[package\nname = ",
        ".github/dependabot.yml": dependabot(CATCH_ALL),
    },
    # A Python project outside the root is not read, and says so.
    "nested_pyproject": {
        "sub/pyproject.toml": MATURIN_PYPROJECT.format(spec="==1.13.3"),
        ".github/dependabot.yml": dependabot(CATCH_ALL, ecosystem="pip"),
    },
    # Nothing declared: no relationship applies.
    "no_declarations": {".github/dependabot.yml": dependabot(CATCH_ALL)},
}


def build_scenario(files: dict[str, str]) -> DependencyFamilyEnvelope:
    """Lay *files* out as a checkout and return the production envelope."""
    with tempfile.TemporaryDirectory(prefix="dependency-family-fixture-") as scratch:
        checkout = Path(scratch)
        for relative, text in files.items():
            path = checkout / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        envelope = build_dependency_family_envelope(checkout)
    envelope["repository"]["path"] = RECORDED_REPOSITORY_PATH
    return envelope


def render() -> str:
    """Return the bundled fixtures document as text."""
    fixtures = {name: build_scenario(files) for name, files in SCENARIOS.items()}
    wrong_kind = {**fixtures["compliant_family"], "kind": "policy-input/other"}
    fixtures["wrong_kind"] = typ.cast("DependencyFamilyEnvelope", wrong_kind)
    return json.dumps({"fixtures": fixtures}, indent=2, sort_keys=False) + "\n"


def main() -> None:
    """Write `data.json` beside this script."""
    DATA_PATH.write_text(render(), encoding="utf-8")


if __name__ == "__main__":
    main()
