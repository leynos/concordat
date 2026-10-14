"""Parse and validate the published Rust consumer seed contracts."""

from __future__ import annotations

import collections.abc as cabc
import tomllib
import typing as typ
from pathlib import Path

import ruamel.yaml

from tests.unit.rust_policy_seed_labels import (
    Seed,
    _as_mapping,
)
from tests.unit.rust_policy_seed_labels import (
    _label_seed_issues as _label_seed_issues,
)

ROOT = Path(__file__).resolve().parents[2]
CANON = ROOT / "platform-standards" / "canon"
YAML = ruamel.yaml.YAML(typ="safe")

CLIPPY_SEED = Path("lint/rust/clippy.toml")
DEPENDABOT_SEED = Path("dependabot/rust-dependabot.yml")
LABEL_SEED = Path("labels/repository-labels.yaml")

_EXPECTED_CLIPPY_VALUES = {
    "cognitive-complexity-threshold": 9,
    "too-many-arguments-threshold": 4,
    "too-many-lines-threshold": 70,
    "excessive-nesting-threshold": 4,
}
_EXPECTED_DISALLOWED_METHODS = {
    "std::env::var": "inject an environment reader",
    "std::env::var_os": "inject an environment reader",
    "std::env::vars": "inject an environment reader",
    "std::env::vars_os": "inject an environment reader",
    "std::env::set_var": "use a stub environment in tests",
    "std::env::remove_var": "use a stub environment in tests",
    "std::env::set_current_dir": (
        "inject a base-directory seam; confine CWD changes to Command::current_dir"
    ),
}
_EXPECTED_ECOSYSTEMS = {"cargo", "github-actions", "rust-toolchain"}
_EXPECTED_DEPENDABOT_LABELS = {
    "cargo": ["dependencies", "cargo"],
    "github-actions": ["dependencies", "github-actions"],
    "rust-toolchain": ["dependencies", "rust-toolchain"],
}
_EXPECTED_DEPENDABOT_COOLDOWNS = {
    "cargo": {"default-days": 7, "semver-major-days": 14},
    "github-actions": {"default-days": 7},
}
type SeedMutator = cabc.Callable[[Seed], None]


def _load_seed(relative_path: Path) -> Seed:
    """Load one checked-in seed as mutable structured data."""
    document = YAML.load((CANON / relative_path).read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        msg = f"Expected a mapping in {relative_path}"
        raise TypeError(msg)
    return typ.cast("Seed", document)


def _load_clippy_seed() -> Seed:
    """Parse the canonical Clippy TOML without treating it as YAML."""
    document = tomllib.loads((CANON / CLIPPY_SEED).read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        msg = f"Expected a TOML table in {CLIPPY_SEED}"
        raise TypeError(msg)
    return typ.cast("Seed", document)


def _clippy_seed_issues(seed: object) -> tuple[str, ...]:
    """Return violations of the shipped Clippy configuration contract."""
    document = _as_mapping(seed)
    if document is None:
        return ("seed must be a mapping",)

    issues = _clippy_value_issues(document)
    issues.extend(_clippy_method_issues(document.get("disallowed-methods")))
    return tuple(issues)


def _clippy_value_issues(document: Seed) -> list[str]:
    """Check the exact approved Clippy thresholds and test-expect setting."""
    issues: list[str] = []
    for key, expected in _EXPECTED_CLIPPY_VALUES.items():
        actual = document.get(key)
        if type(actual) is not int or actual != expected:
            issues.append(f"{key} must be {expected}")
    if document.get("allow-expect-in-tests") is not True:
        issues.append("allow-expect-in-tests must be true")
    return issues


def _clippy_method_issues(configured_methods: object) -> list[str]:
    """Check required Clippy methods, reason pairs, and duplicate paths."""
    if not isinstance(configured_methods, list):
        return ["disallowed-methods must be a list"]

    issues: list[str] = []
    actual_methods: dict[str, str] = {}
    for rule in configured_methods:
        entry = _clippy_method_entry(rule)
        if isinstance(entry, str):
            issues.append(entry)
            continue
        path, reason = entry
        if path in actual_methods:
            issues.append(f"disallowed method {path} is duplicated")
        actual_methods[path] = reason

    if actual_methods != _EXPECTED_DISALLOWED_METHODS:
        issues.append("disallowed methods and reasons must match the approved seed")
    return issues


def _clippy_method_entry(rule: object) -> tuple[str, str] | str:
    """Return a valid path/reason pair or its ordered entry-level diagnostic."""
    method = _as_mapping(rule)
    if method is None:
        return "each disallowed method must have path and reason"
    if set(method) != {"path", "reason"}:
        return "each disallowed method must have path and reason"

    path = method.get("path")
    reason = method.get("reason")
    if not isinstance(path, str):
        return "disallowed method path and reason must be strings"
    if not isinstance(reason, str):
        return "disallowed method path and reason must be strings"
    return path, reason


def _update_for(document: Seed, ecosystem: str) -> Seed:
    """Return the unique update record for one ecosystem in a test copy."""
    updates = document.get("updates")
    if not isinstance(updates, list):
        msg = "Dependabot updates must be a list"
        raise TypeError(msg)
    matches = [
        update
        for update in updates
        if isinstance(update, dict) and update.get("package-ecosystem") == ecosystem
    ]
    if len(matches) != 1:
        msg = f"Expected one {ecosystem} update entry"
        raise AssertionError(msg)
    return typ.cast("Seed", matches[0])


def _named_dependabot_updates(
    updates_value: cabc.Sequence[object],
) -> tuple[dict[str, Seed], list[str]]:
    """Collect named update entries while preserving duplicate diagnostics."""
    updates: dict[str, Seed] = {}
    issues: list[str] = []
    for value in updates_value:
        entry = _named_dependabot_entry(value)
        if isinstance(entry, str):
            issues.append(entry)
            continue
        ecosystem, update = entry
        if ecosystem in updates:
            issues.append(f"{ecosystem} update entry is duplicated")
        else:
            updates[ecosystem] = update
    if set(updates) != _EXPECTED_ECOSYSTEMS:
        issues.append("supported ecosystems must be Cargo, GitHub Actions, and Rust")
    return updates, issues


def _named_dependabot_entry(value: object) -> tuple[str, Seed] | str:
    """Return an ecosystem/update pair or the existing row diagnostic."""
    update = _as_mapping(value)
    if update is None:
        return "every update must name a package ecosystem"
    ecosystem = update.get("package-ecosystem")
    if not isinstance(ecosystem, str):
        return "every update must name a package ecosystem"
    return ecosystem, update


def _dependabot_route_issues(ecosystem: str, update: Seed) -> list[str]:
    """Check root and local-action coverage for a supported ecosystem."""
    if ecosystem == "github-actions":
        directories = update.get("directories")
        issues = []
        if directories != ["/", "/.github/actions/*"]:
            issues.append("GitHub Actions must cover root and local actions")
        if "directory" in update:
            issues.append("GitHub Actions must use the directories form")
        return issues
    if update.get("directory") != "/" or "directories" in update:
        return [f"{ecosystem} must use the root directory"]
    return []


def _dependabot_group_issues(ecosystem: str, update: Seed) -> list[str]:
    """Accumulate final group-order and content diagnostics."""
    groups = _as_mapping(update.get("groups"))
    issues = _dependabot_group_order_issues(ecosystem, groups)
    issues.extend(_dependabot_group_content_issues(ecosystem, groups))
    return issues


def _dependabot_group_order_issues(ecosystem: str, groups: Seed | None) -> list[str]:
    """Require the minor-and-patch group to be the final configured group."""
    if groups is not None and not _minor_patch_group_is_last(groups):
        return [f"{ecosystem} minor-and-patch group must be last"]
    return []


def _minor_patch_group_is_last(groups: Seed) -> bool:
    """Return whether a non-empty group map ends in the required group."""
    return bool(groups) and next(reversed(groups)) == "minor-and-patch"


def _dependabot_group_content_issues(ecosystem: str, groups: Seed | None) -> list[str]:
    """Check group presence, package patterns, and supported update types."""
    issues: list[str] = []
    minor_patch = None if groups is None else _as_mapping(groups.get("minor-and-patch"))
    if minor_patch is None:
        issues.append(f"{ecosystem} must define the minor-and-patch group")
        return issues
    if minor_patch.get("patterns") != ["*"]:
        issues.append(f"{ecosystem} group must cover all package patterns")
    if minor_patch.get("update-types") != ["minor", "patch"]:
        issues.append(f"{ecosystem} group must include minor and patch updates")
    return issues


def _dependabot_cooldown_issues(ecosystem: str, update: Seed) -> list[str]:
    """Check only the approved Cargo and GitHub Actions cooldown values."""
    expected_cooldown = _EXPECTED_DEPENDABOT_COOLDOWNS.get(ecosystem)
    if expected_cooldown is None:
        return []
    cooldown = _as_mapping(update.get("cooldown"))
    if cooldown != expected_cooldown:
        return [f"{ecosystem} cooldown must match the approved values"]
    return []


def _dependabot_update_issues(ecosystem: str, update: Seed) -> list[str]:
    """Accumulate a supported ecosystem's route and update policy issues."""
    issues = _dependabot_route_issues(ecosystem, update)
    checks = (
        (
            update.get("schedule") == {"interval": "daily"},
            f"{ecosystem} must use the daily schedule",
        ),
        (
            type(update.get("open-pull-requests-limit")) is int
            and update.get("open-pull-requests-limit") == 5,
            f"{ecosystem} pull request limit must be 5",
        ),
        (
            update.get("labels") == _EXPECTED_DEPENDABOT_LABELS[ecosystem],
            f"{ecosystem} labels must match the approved seed",
        ),
    )
    for is_valid, diagnostic in checks:
        if not is_valid:
            issues.append(diagnostic)
    issues.extend(_dependabot_group_issues(ecosystem, update))
    issues.extend(_dependabot_cooldown_issues(ecosystem, update))
    return issues


def _dependabot_seed_issues(seed: object) -> tuple[str, ...]:
    """Return violations of the shipped Dependabot template contract."""
    document = _as_mapping(seed)
    if document is None:
        return ("seed must be a mapping",)
    if document.get("version") != 2:
        return ("Dependabot version must be 2",)
    updates_value = document.get("updates")
    if not isinstance(updates_value, list):
        return ("updates must be a list",)

    updates, issues = _named_dependabot_updates(updates_value)
    for ecosystem in sorted(_EXPECTED_ECOSYSTEMS & set(updates)):
        issues.extend(_dependabot_update_issues(ecosystem, updates[ecosystem]))

    return tuple(issues)
