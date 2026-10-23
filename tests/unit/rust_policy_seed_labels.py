"""Validate parsed seed mappings and the published label snapshot contract."""

from __future__ import annotations

import hashlib
import json
import re
import typing as typ

from tests.unit.rust_policy_label_expectations import (
    ADDED_LABEL_NAMES,
    APPROVED_LABELS,
    PROJECTION_SHA256,
)

_SOURCE_FIELDS = {
    "repository",
    "api",
    "observed_at",
    "capture_command",
    "captured_json_sha256",
    "captured_label_count",
    "raw_json_verification",
    "approval_reference",
}
_EXPECTED_SOURCE_VALUES = {
    "repository": "leynos/netsuke",
    "api": "https://api.github.com/repos/leynos/netsuke/labels",
    "capture_command": "gh api repos/leynos/netsuke/labels --paginate",
    "approval_reference": "leynos/concordat#157",
}
_EXPECTED_LABEL_NAMES = frozenset(APPROVED_LABELS)
_EXPECTED_CAPTURE_TIME = "2026-09-08T10:47:29Z"
_EXPECTED_CAPTURE_HASH = (
    "bdd8fa7d9da6e7ef6d2c46a1db2cddff0e803b95797bcae55cad26c18d513133"
)
_EXPECTED_POLICY = {
    "approval_reference": (
        "https://github.com/leynos/concordat/issues/157#issuecomment-6213191578"
    ),
    "snapshot_label_count": 31,
    "added_labels": list(ADDED_LABEL_NAMES),
    "projection_serialization": "json-sort-keys-compact-utf8",
    "projection_sha256": PROJECTION_SHA256,
}

type Seed = dict[str, object]


def _as_mapping(value: object) -> Seed | None:
    """Narrow parsed YAML/TOML values to string-keyed tables."""
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        return typ.cast("Seed", value)
    return None


def _label_seed_issues(seed: object) -> tuple[str, ...]:
    """Return violations of the dated label snapshot and GitHub API bounds."""
    document = _as_mapping(seed)
    if document is None:
        return ("seed must be a mapping",)
    if set(document) != {"schema_version", "source", "policy", "labels"}:
        return ("label snapshot must use the published top-level shape",)
    if (
        type(document.get("schema_version")) is not int
        or document.get("schema_version") != 2
    ):
        return ("label snapshot schema_version must be 2",)

    issues = _label_source_issues(document.get("source"))
    issues.extend(_label_policy_issues(document.get("policy")))
    issues.extend(_label_records_issues(document.get("labels")))
    return tuple(issues)


def _label_records_issues(labels_value: object) -> list[str]:
    """Check record bounds, approved values, completeness, and projection bytes."""
    if not isinstance(labels_value, list):
        return ["labels must be a list"]
    issues: list[str] = []
    names: list[str] = []
    valid_records: list[Seed] = []
    for label_value in labels_value:
        record_issues, name = _label_record_issues(label_value)
        issues.extend(record_issues)
        record = _as_mapping(label_value)
        if not record_issues and record is not None:
            valid_records.append(record)
        if name is not None:
            names.append(name)
    issues.extend(_label_snapshot_name_issues(names))
    if len(valid_records) == len(labels_value):
        issues.extend(_label_projection_issues(valid_records))
    return issues


def _label_source_issues(source_value: object) -> list[str]:
    """Check source shape, approved identities, timestamp, and digest."""
    source = _as_mapping(source_value)
    if source is None:
        return ["label source must carry every published provenance field"]
    if set(source) != _SOURCE_FIELDS:
        return ["label source must carry every published provenance field"]

    issues = _label_source_identity_issues(source)
    issues.extend(_label_capture_time_issues(source.get("observed_at")))
    issues.extend(_label_capture_hash_issues(source.get("captured_json_sha256")))
    if (
        type(source.get("captured_label_count")) is not int
        or source.get("captured_label_count") != 31
    ):
        issues.append("source captured_label_count must attest the 31-label capture")
    if source.get("raw_json_verification") != "unavailable":
        issues.append("raw API content verification must remain unavailable")
    return issues


def _label_source_identity_issues(source: Seed) -> list[str]:
    """Require the exact repository, API, command, and approval reference."""
    issues: list[str] = []
    for key, expected in _EXPECTED_SOURCE_VALUES.items():
        if source.get(key) != expected:
            issues.append(f"source {key} must match the approved snapshot")
    return issues


def _label_capture_time_issues(observed_at: object) -> list[str]:
    """Require the exact approved UTC timestamp, rejecting other values."""
    if not isinstance(observed_at, str) or observed_at != _EXPECTED_CAPTURE_TIME:
        return ["source observed_at must match the approved snapshot"]
    return []


def _label_capture_hash_issues(captured_hash: object) -> list[str]:
    """Require the exact approved lowercase SHA-256 attestation."""
    if not isinstance(captured_hash, str) or captured_hash != _EXPECTED_CAPTURE_HASH:
        return ["source captured_json_sha256 must match the approved snapshot"]
    return []


def _label_record_issues(label_value: object) -> tuple[list[str], str | None]:
    """Validate one label record and return its valid name, if present."""
    label = _as_mapping(label_value)
    if label is None:
        return ["each label must use the published name/color/description shape"], None
    if set(label) != {"name", "color", "description"}:
        return ["each label must use the published name/color/description shape"], None

    issues, name = _label_name_field_issues(label.get("name"))
    issues.extend(_label_color_field_issues(label.get("color")))
    issues.extend(_label_description_field_issues(label.get("description")))
    if name is not None:
        issues.extend(_label_content_issues(label, name))
    return issues, name


def _label_content_issues(label: Seed, name: str) -> list[str]:
    """Compare known labels with their independently approved policy values."""
    expected_content = APPROVED_LABELS.get(name)
    if expected_content is None:
        return []
    if (label.get("color"), label.get("description")) != expected_content:
        return ["label colours and descriptions must match the approved policy"]
    return []


def _label_name_field_issues(name_value: object) -> tuple[list[str], str | None]:
    """Enforce GitHub's one-to-fifty-character label-name limit."""
    if not isinstance(name_value, str):
        return ["label names must contain between 1 and 50 characters"], None
    if not 0 < len(name_value) <= 50:
        return ["label names must contain between 1 and 50 characters"], None
    return [], name_value


def _label_color_field_issues(color_value: object) -> list[str]:
    """Require a six-digit hexadecimal GitHub label colour."""
    if not isinstance(color_value, str):
        return ["label colors must contain six hexadecimal digits"]
    if re.fullmatch(r"[0-9a-fA-F]{6}", color_value) is None:
        return ["label colors must contain six hexadecimal digits"]
    return []


def _label_description_field_issues(description: object) -> list[str]:
    """Require descriptions to be absent or at most one hundred characters."""
    if description is None:
        return []
    if not isinstance(description, str):
        return ["label descriptions must be null or at most 100 characters"]
    if len(description) > 100:
        return ["label descriptions must be null or at most 100 characters"]
    return []


def _label_snapshot_name_issues(names: list[str]) -> list[str]:
    """Require unique names and the complete published label set."""
    issues: list[str] = []
    if len(names) != len(set(names)):
        issues.append("label names must be unique")
    if set(names) != _EXPECTED_LABEL_NAMES:
        issues.append("label names must match the complete approved snapshot")
    return issues


def _label_policy_issues(policy_value: object) -> list[str]:
    """Keep approved additions separate from the historical API attestation."""
    policy = _as_mapping(policy_value)
    if policy is None or policy != _EXPECTED_POLICY:
        return ["label policy must match its independent approval and projection"]
    if type(policy.get("snapshot_label_count")) is not int:
        return ["policy snapshot_label_count must be an integer"]
    return []


def _label_projection_issues(records: list[Seed]) -> list[str]:
    """Verify projected policy bytes without claiming to verify raw API bytes."""
    projection = sorted(records, key=lambda record: str(record["name"]))
    payload = json.dumps(
        projection, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    if hashlib.sha256(payload).hexdigest() != PROJECTION_SHA256:
        return ["label projection must match its reproducible policy digest"]
    return []
