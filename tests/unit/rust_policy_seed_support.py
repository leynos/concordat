"""Parse and validate the published Rust consumer seed contracts."""

from __future__ import annotations

import collections.abc as cabc
import datetime as dt
import re
import tomllib
import typing as typ
from pathlib import Path

import ruamel.yaml

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
_SOURCE_FIELDS = {
    "repository",
    "api",
    "observed_at",
    "capture_command",
    "captured_json_sha256",
    "approval_reference",
}
_EXPECTED_LABEL_NAMES = frozenset({
    "bug",
    "bun",
    "cargo",
    "codex",
    "concurrency",
    "critical",
    "dependencies",
    "documentation",
    "duplicate",
    "enhancement",
    "epic",
    "github-actions",
    "go",
    "good first issue",
    "help wanted",
    "high",
    "invalid",
    "Issue",
    "low",
    "medium",
    "opentofu",
    "performance",
    "pip",
    "question",
    "refactor",
    "Roadmap",
    "rust-toolchain",
    "testing",
    "uv",
    "Windows",
    "wontfix",
})
_EXPECTED_LABEL_CAPTURE_TIME = "2026-09-30T11:38:30Z"
_EXPECTED_LABEL_CAPTURE_HASH = (
    "dad7045b340d12e4393f7e94b61d7aea01073fe7faa17075368de29785f5103a"
)

type Seed = dict[str, object]
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


def _as_mapping(value: object) -> dict[str, object] | None:
    """Narrow parsed YAML/TOML values to string-keyed tables."""
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        return typ.cast("Seed", value)
    return None


def _clippy_seed_issues(seed: object) -> tuple[str, ...]:
    """Return violations of the shipped Clippy configuration contract."""
    document = _as_mapping(seed)
    if document is None:
        return ("seed must be a mapping",)

    issues: list[str] = []
    for key, expected in _EXPECTED_CLIPPY_VALUES.items():
        actual = document.get(key)
        if type(actual) is not int or actual != expected:
            issues.append(f"{key} must be {expected}")
    if document.get("allow-expect-in-tests") is not True:
        issues.append("allow-expect-in-tests must be true")

    configured_methods = document.get("disallowed-methods")
    if not isinstance(configured_methods, list):
        issues.append("disallowed-methods must be a list")
        return tuple(issues)

    actual_methods: dict[str, str] = {}
    for rule in configured_methods:
        method = _as_mapping(rule)
        if method is None or set(method) != {"path", "reason"}:
            issues.append("each disallowed method must have path and reason")
            continue
        path = method.get("path")
        reason = method.get("reason")
        if not isinstance(path, str) or not isinstance(reason, str):
            issues.append("disallowed method path and reason must be strings")
            continue
        if path in actual_methods:
            issues.append(f"disallowed method {path} is duplicated")
        actual_methods[path] = reason

    if actual_methods != _EXPECTED_DISALLOWED_METHODS:
        issues.append("disallowed methods and reasons must match the approved seed")
    return tuple(issues)


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

    updates: dict[str, Seed] = {}
    issues: list[str] = []
    for value in updates_value:
        update = _as_mapping(value)
        ecosystem = None if update is None else update.get("package-ecosystem")
        if not isinstance(ecosystem, str):
            issues.append("every update must name a package ecosystem")
            continue
        if ecosystem in updates:
            issues.append(f"{ecosystem} update entry is duplicated")
        elif update is not None:
            updates[ecosystem] = update

    if set(updates) != _EXPECTED_ECOSYSTEMS:
        issues.append("supported ecosystems must be Cargo, GitHub Actions, and Rust")

    for ecosystem in sorted(_EXPECTED_ECOSYSTEMS & set(updates)):
        update = updates[ecosystem]
        if ecosystem == "github-actions":
            directories = update.get("directories")
            if directories != ["/", "/.github/actions/*"]:
                issues.append("GitHub Actions must cover root and local actions")
            if "directory" in update:
                issues.append("GitHub Actions must use the directories form")
        elif update.get("directory") != "/" or "directories" in update:
            issues.append(f"{ecosystem} must use the root directory")

        if update.get("schedule") != {"interval": "daily"}:
            issues.append(f"{ecosystem} must use the daily schedule")
        if (
            type(update.get("open-pull-requests-limit")) is not int
            or update.get("open-pull-requests-limit") != 5
        ):
            issues.append(f"{ecosystem} pull request limit must be 5")
        if update.get("labels") != _EXPECTED_DEPENDABOT_LABELS[ecosystem]:
            issues.append(f"{ecosystem} labels must match the approved seed")

        groups = _as_mapping(update.get("groups"))
        if groups is not None and (
            not groups or next(reversed(groups)) != "minor-and-patch"
        ):
            issues.append(f"{ecosystem} minor-and-patch group must be last")
        minor_patch = (
            None if groups is None else _as_mapping(groups.get("minor-and-patch"))
        )
        if minor_patch is None:
            issues.append(f"{ecosystem} must define the minor-and-patch group")
        else:
            if minor_patch.get("patterns") != ["*"]:
                issues.append(f"{ecosystem} group must cover all package patterns")
            if minor_patch.get("update-types") != ["minor", "patch"]:
                issues.append(f"{ecosystem} group must include minor and patch updates")

        # Rust-toolchain has no specified cooldown requirement. Keep checks below
        # scoped to the two ecosystems whose cooldown values are part of this seed.
        if ecosystem in {"cargo", "github-actions"}:
            cooldown = _as_mapping(update.get("cooldown"))
            expected_cooldown: dict[str, object] = {"default-days": 7}
            if ecosystem == "cargo":
                expected_cooldown["semver-major-days"] = 14
            if cooldown != expected_cooldown:
                issues.append(f"{ecosystem} cooldown must match the approved values")

    return tuple(issues)


def _label_seed_issues(seed: object) -> tuple[str, ...]:
    """Return violations of the dated label snapshot and GitHub API bounds."""
    document = _as_mapping(seed)
    if document is None:
        return ("seed must be a mapping",)
    if set(document) != {"schema_version", "source", "labels"}:
        return ("label snapshot must use the published top-level shape",)
    if (
        type(document.get("schema_version")) is not int
        or document.get("schema_version") != 1
    ):
        return ("label snapshot schema_version must be 1",)

    issues: list[str] = []
    source = _as_mapping(document.get("source"))
    if source is None or set(source) != _SOURCE_FIELDS:
        issues.append("label source must carry every published provenance field")
    else:
        expected_source_values = {
            "repository": "leynos/netsuke",
            "api": "https://api.github.com/repos/leynos/netsuke/labels",
            "capture_command": "gh api repos/leynos/netsuke/labels --paginate",
            "approval_reference": "leynos/concordat#157",
        }
        for key, expected in expected_source_values.items():
            if source.get(key) != expected:
                issues.append(f"source {key} must match the approved snapshot")

        observed_at = source.get("observed_at")
        try:
            parsed_time = (
                dt.datetime.fromisoformat(observed_at)
                if isinstance(observed_at, str)
                else None
            )
        except ValueError:
            parsed_time = None
        if parsed_time is None or parsed_time.utcoffset() != dt.timedelta(0):
            issues.append("source observed_at must be a UTC timestamp")
        if observed_at != _EXPECTED_LABEL_CAPTURE_TIME:
            issues.append("source observed_at must match the approved snapshot")

        captured_hash = source.get("captured_json_sha256")
        if not isinstance(captured_hash, str) or not re.fullmatch(
            r"[0-9a-f]{64}", captured_hash
        ):
            issues.append("source captured_json_sha256 must be a lowercase SHA-256")
        if captured_hash != _EXPECTED_LABEL_CAPTURE_HASH:
            issues.append(
                "source captured_json_sha256 must match the approved snapshot"
            )

    labels_value = document.get("labels")
    if not isinstance(labels_value, list):
        issues.append("labels must be a list")
        return tuple(issues)

    names: list[str] = []
    for label_value in labels_value:
        label = _as_mapping(label_value)
        if label is None or set(label) != {"name", "color", "description"}:
            issues.append(
                "each label must use the published name/color/description shape"
            )
            continue

        name = label.get("name")
        if not isinstance(name, str) or not 0 < len(name) <= 50:
            issues.append("label names must contain between 1 and 50 characters")
        else:
            names.append(name)

        color = label.get("color")
        if not isinstance(color, str) or re.fullmatch(r"[0-9a-fA-F]{6}", color) is None:
            issues.append("label colors must contain six hexadecimal digits")

        description = label.get("description")
        if description is not None and (
            not isinstance(description, str) or len(description) > 100
        ):
            issues.append("label descriptions must be null or at most 100 characters")

    if len(names) != len(set(names)):
        issues.append("label names must be unique")
    if set(names) != _EXPECTED_LABEL_NAMES:
        issues.append("label names must match the complete approved snapshot")
    return tuple(issues)
