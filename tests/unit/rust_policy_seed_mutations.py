"""Apply isolated mutations to parsed Rust consumer seed copies."""

from __future__ import annotations

import typing as typ

from tests.unit.rust_policy_seed_support import Seed, _as_mapping, _update_for


def _set_update_field(
    document: Seed, ecosystem: str, field: str, value: object
) -> None:
    """Replace one field in the named Dependabot update entry."""
    _update_for(document, ecosystem)[field] = value


def _change_clippy_threshold(seed: Seed) -> None:
    """Raise one Clippy threshold to simulate an unapproved relaxation."""
    seed["cognitive-complexity-threshold"] = 10


def _remove_clippy_method(seed: Seed) -> None:
    """Remove one required method from the Clippy disallow list."""
    methods = seed["disallowed-methods"]
    if not isinstance(methods, list):
        msg = "Clippy disallowed-methods must be a list"
        raise TypeError(msg)
    seed["disallowed-methods"] = [
        method
        for method in methods
        if not isinstance(method, dict) or method.get("path") != "std::env::var"
    ]


def _change_clippy_reason(seed: Seed) -> None:
    """Change a required Clippy reason while preserving its method path."""
    methods = seed["disallowed-methods"]
    if not isinstance(methods, list):
        msg = "Clippy disallowed-methods must be a list"
        raise TypeError(msg)
    for method in methods:
        if isinstance(method, dict) and method.get("path") == "std::env::var":
            method_record = typ.cast("dict[str, object]", method)
            method_record["reason"] = "read environment variables directly"
            return
    msg = "Expected std::env::var in the shipped seed"
    raise AssertionError(msg)


def _change_dependabot_schedule(seed: Seed) -> None:
    """Change Cargo update cadence from daily to weekly."""
    _set_update_field(seed, "cargo", "schedule", {"interval": "weekly"})


def _change_dependabot_limit(seed: Seed) -> None:
    """Raise the configured Cargo pull request limit."""
    _set_update_field(seed, "cargo", "open-pull-requests-limit", 6)


def _change_dependabot_directories(seed: Seed) -> None:
    """Drop the local-action directory from GitHub Actions updates."""
    _set_update_field(seed, "github-actions", "directories", ["/"])


def _replace_cargo_cooldown(
    seed: Seed, default_days: int, semver_major_days: int
) -> None:
    """Replace both Cargo cooldown values without changing the update record."""
    _set_update_field(
        seed,
        "cargo",
        "cooldown",
        {
            "default-days": default_days,
            "semver-major-days": semver_major_days,
        },
    )


def _change_cargo_default_cooldown(seed: Seed) -> None:
    """Change Cargo's required default cooldown."""
    _replace_cargo_cooldown(seed, default_days=8, semver_major_days=14)


def _change_cargo_major_cooldown(seed: Seed) -> None:
    """Change Cargo's required major-version cooldown."""
    _replace_cargo_cooldown(seed, default_days=7, semver_major_days=15)


def _change_actions_cooldown(seed: Seed) -> None:
    """Change GitHub Actions' required default cooldown."""
    _set_update_field(seed, "github-actions", "cooldown", {"default-days": 8})


def _minor_patch_groups(seed: Seed) -> dict[str, object]:
    """Return the Cargo groups mapping after checking its required entry."""
    update = _update_for(seed, "cargo")
    if "groups" not in update:
        msg = "Expected minor-and-patch group in the shipped seed"
        raise AssertionError(msg)
    groups = _as_mapping(update["groups"])
    if groups is None:
        msg = "Expected minor-and-patch group in the shipped seed"
        raise TypeError(msg)
    if "minor-and-patch" not in groups:
        msg = "Expected minor-and-patch group in the shipped seed"
        raise AssertionError(msg)
    return groups


def _minor_patch_group(seed: Seed) -> dict[str, object]:
    """Return the required group mapping, preserving invalid-type failures."""
    groups = _minor_patch_groups(seed)
    group = _as_mapping(groups["minor-and-patch"])
    if group is None:
        msg = "Expected minor-and-patch group in the shipped seed"
        raise TypeError(msg)
    return group


def _change_dependabot_group_types(seed: Seed) -> None:
    """Remove patch updates from the minor-and-patch group."""
    group = _minor_patch_group(seed)
    group["update-types"] = ["minor"]


def _change_dependabot_group_patterns(seed: Seed) -> None:
    """Narrow the minor-and-patch group to a package-name pattern."""
    group = _minor_patch_group(seed)
    group["patterns"] = ["cargo-*"]


def _insert_group_after_minor_patch(seed: Seed) -> None:
    """Append a group to detect loss of the required final-group ordering."""
    groups = _minor_patch_groups(seed)
    groups["later-group"] = {"patterns": ["*"], "update-types": ["major"]}


def _label_source(seed: Seed) -> dict[str, object]:
    """Return mutable label provenance, preserving type and presence failures."""
    if "source" not in seed:
        msg = "Expected source provenance in the shipped seed"
        raise AssertionError(msg)
    source = _as_mapping(seed["source"])
    if source is None:
        msg = "Expected source provenance in the shipped seed"
        raise TypeError(msg)
    return source


def _remove_label_approval(seed: Seed) -> None:
    """Remove the required approval reference from snapshot provenance."""
    source = _label_source(seed)
    source.pop("approval_reference")


def _change_label_hash(seed: Seed) -> None:
    """Replace the recorded hash with a different but well-formed digest."""
    source = _label_source(seed)
    source["captured_json_sha256"] = "0" * 64


def _change_label_capture_time(seed: Seed) -> None:
    """Replace the recorded date with a different valid UTC timestamp."""
    source = _label_source(seed)
    source["observed_at"] = "2026-10-01T11:38:30Z"


def _drop_label(seed: Seed) -> None:
    """Drop a published label while leaving the snapshot metadata intact."""
    labels = seed["labels"]
    if not isinstance(labels, list):
        msg = "Expected label records in the shipped seed"
        raise TypeError(msg)
    if not labels:
        msg = "Expected label records in the shipped seed"
        raise AssertionError(msg)
    labels.pop()


def _overrun_label_name(seed: Seed) -> None:
    """Exceed GitHub's maximum label-name length."""
    labels = seed["labels"]
    if not isinstance(labels, list):
        msg = "Expected label records in the shipped seed"
        raise TypeError(msg)
    if not labels:
        msg = "Expected label records in the shipped seed"
        raise AssertionError(msg)
    if not isinstance(labels[0], dict):
        msg = "Expected label records in the shipped seed"
        raise TypeError(msg)
    label = typ.cast("dict[str, object]", labels[0])
    label["name"] = "x" * 51


def _duplicate_label_name(seed: Seed) -> None:
    """Duplicate a name to violate the snapshot's unique-label contract."""
    labels = seed["labels"]
    if not isinstance(labels, list):
        msg = "Expected label records in the shipped seed"
        raise TypeError(msg)
    if len(labels) < 2:
        msg = "Expected label records in the shipped seed"
        raise AssertionError(msg)
    if not isinstance(labels[0], dict) or not isinstance(labels[1], dict):
        msg = "Expected label records in the shipped seed"
        raise TypeError(msg)
    first_label = typ.cast("dict[str, object]", labels[0])
    second_label = typ.cast("dict[str, object]", labels[1])
    second_label["name"] = first_label["name"]
