"""Pin approved label values while separating API attestations from policy data."""

from __future__ import annotations

import copy
import hashlib
import json
import typing as typ

import pytest

from tests.unit.rust_policy_label_expectations import (
    ADDED_LABEL_NAMES,
    APPROVED_LABELS,
    PROJECTION_SHA256,
)
from tests.unit.rust_policy_seed_support import (
    LABEL_SEED,
    Seed,
    _label_seed_issues,
    _load_seed,
)

_CAPTURE_HASH = "bdd8fa7d9da6e7ef6d2c46a1db2cddff0e803b95797bcae55cad26c18d513133"
_APPROVAL_REFERENCE = (
    "https://github.com/leynos/concordat/issues/157#issuecomment-6213191578"
)
_SERIALIZATION = "json-sort-keys-compact-utf8"


def _label_records(seed: Seed) -> list[Seed]:
    """Narrow parsed records without substituting or repairing seed contents."""
    records = seed["labels"]
    assert isinstance(records, list)
    assert all(isinstance(record, dict) for record in records)
    return typ.cast("list[Seed]", records)


def _projection_digest(records: list[Seed]) -> str:
    """Hash only name, colour, and description, in case-sensitive name order."""
    projection = [
        {key: record[key] for key in ("name", "color", "description")}
        for record in sorted(records, key=lambda record: str(record["name"]))
    ]
    payload = json.dumps(
        projection, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def test_label_policy_matches_every_independently_approved_value() -> None:
    """All 34 names, exact colours, descriptions, nulls, and case are pinned."""
    records = _label_records(_load_seed(LABEL_SEED))
    actual = {
        record["name"]: (record["color"], record["description"]) for record in records
    }
    assert len(records) == len(actual) == 34
    assert actual == APPROVED_LABELS


def test_label_capture_is_an_external_31_label_attestation() -> None:
    """Git source and projected YAML cannot establish unavailable raw API bytes."""
    seed = _load_seed(LABEL_SEED)
    assert seed["schema_version"] == 2
    assert seed["source"] == {
        "repository": "leynos/netsuke",
        "api": "https://api.github.com/repos/leynos/netsuke/labels",
        "observed_at": "2026-09-08T10:47:29Z",
        "capture_command": "gh api repos/leynos/netsuke/labels --paginate",
        "captured_json_sha256": _CAPTURE_HASH,
        "approval_reference": "leynos/concordat#157",
        "captured_label_count": 31,
        "raw_json_verification": "unavailable",
    }


def test_label_policy_additions_have_a_separate_reproducible_digest() -> None:
    """Later additions belong to the approved policy, not the historical capture."""
    seed = _load_seed(LABEL_SEED)
    assert seed["policy"] == {
        "approval_reference": _APPROVAL_REFERENCE,
        "snapshot_label_count": 31,
        "added_labels": list(ADDED_LABEL_NAMES),
        "projection_serialization": _SERIALIZATION,
        "projection_sha256": PROJECTION_SHA256,
    }
    assert _projection_digest(_label_records(seed)) == PROJECTION_SHA256
    assert PROJECTION_SHA256 != _CAPTURE_HASH


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        pytest.param("color", "000000", id="valid_colour_drift"),
        pytest.param(
            "description", "A changed valid description", id="valid_description_drift"
        ),
    ],
)
def test_label_validator_rejects_valid_value_drift(
    field: str, replacement: str
) -> None:
    """API-valid changes must fail the approved model, even with a new digest."""
    candidate = copy.deepcopy(_load_seed(LABEL_SEED))
    assert _label_seed_issues(candidate) == ()
    records = _label_records(candidate)
    records[0][field] = replacement
    policy = candidate["policy"]
    assert isinstance(policy, dict)
    assert all(isinstance(key, str) for key in policy)
    policy_fields = typ.cast("Seed", policy)
    policy_fields["projection_sha256"] = _projection_digest(records)
    assert _label_seed_issues(candidate)


@pytest.mark.parametrize(
    ("section", "field", "replacement"),
    [
        pytest.param(
            "source", "captured_label_count", 34, id="additions_not_in_capture"
        ),
        pytest.param(
            "source",
            "raw_json_verification",
            "verified",
            id="no_raw_verification_claim",
        ),
        pytest.param(
            "policy",
            "projection_sha256",
            _CAPTURE_HASH,
            id="raw_hash_not_projection_hash",
        ),
        pytest.param("policy", "added_labels", [], id="additions_retained"),
    ],
)
def test_label_validator_rejects_false_provenance_boundaries(
    section: str, field: str, replacement: object
) -> None:
    """A valid baseline precedes each isolated provenance corruption."""
    candidate = copy.deepcopy(_load_seed(LABEL_SEED))
    assert _label_seed_issues(candidate) == ()
    metadata = candidate[section]
    assert isinstance(metadata, dict)
    assert all(isinstance(key, str) for key in metadata)
    metadata_fields = typ.cast("Seed", metadata)
    metadata_fields[field] = replacement
    assert _label_seed_issues(candidate)
