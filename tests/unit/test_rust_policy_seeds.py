"""Keep published Rust consumer seeds aligned with their approved contracts."""

from __future__ import annotations

import copy
import hashlib
import json
import re
import typing as typ
from pathlib import Path

import pytest

from tests.unit.rust_policy_seed_labels import _EXPECTED_LABEL_CONTENT
from tests.unit.rust_policy_seed_mutations import (
    _bug_label_record,
    _change_actions_cooldown,
    _change_bug_label_color,
    _change_bug_label_description,
    _change_cargo_default_cooldown,
    _change_cargo_major_cooldown,
    _change_clippy_reason,
    _change_clippy_threshold,
    _change_dependabot_directories,
    _change_dependabot_group_patterns,
    _change_dependabot_group_types,
    _change_dependabot_limit,
    _change_dependabot_schedule,
    _change_label_capture_time,
    _change_label_hash,
    _drop_label,
    _duplicate_label_name,
    _insert_group_after_minor_patch,
    _overrun_label_name,
    _remove_clippy_method,
    _remove_label_approval,
)
from tests.unit.rust_policy_seed_support import (
    CANON,
    DEPENDABOT_SEED,
    LABEL_SEED,
    YAML,
    Seed,
    SeedMutator,
    _clippy_seed_issues,
    _dependabot_seed_issues,
    _label_seed_issues,
    _load_clippy_seed,
    _load_seed,
    _update_for,
)


@pytest.fixture
def captured_bug_label() -> dict[str, object]:
    """Load the bug record from the byte-preserved Netsuke API capture."""
    capture_path = (
        Path(__file__).resolve().parents[1]
        / "fixtures"
        / "rust_policy"
        / "netsuke-labels-2026-09-30.json"
    )
    records: object = json.loads(capture_path.read_text(encoding="utf-8"))
    if not isinstance(records, list):
        msg = "Expected a list in the Netsuke label capture"
        raise TypeError(msg)
    bug_records = [
        record
        for record in records
        if isinstance(record, dict) and record.get("name") == "bug"
    ]
    if len(bug_records) != 1:
        msg = "Expected one bug label in the Netsuke label capture"
        raise AssertionError(msg)
    return typ.cast("dict[str, object]", bug_records[0])


def test_rust_policy_seeds_are_registered() -> None:
    """A consumer cannot select a seed omitted from the manifest."""
    manifest = YAML.load((CANON / "manifest.yaml").read_text(encoding="utf-8"))
    registered = {entry["id"]: entry["path"] for entry in manifest["artifacts"]}

    assert registered["rust-clippy-config"] == (
        "platform-standards/canon/lint/rust/clippy.toml"
    )
    assert registered["rust-dependabot-template"] == (
        "platform-standards/canon/dependabot/rust-dependabot.yml"
    )
    assert registered["repository-label-model"] == (
        "platform-standards/canon/labels/repository-labels.yaml"
    )


def test_clippy_seed_contains_the_approved_development_contract() -> None:
    """Consumers receive the documented thresholds and environment boundaries."""
    assert _clippy_seed_issues(_load_clippy_seed()) == ()


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(_change_clippy_threshold, id="make_test_threshold"),
        pytest.param(_remove_clippy_method, id="remove_test_method"),
        pytest.param(_change_clippy_reason, id="change_test_reason"),
    ],
)
def test_seed_contracts_reject_clippy_mutations(mutate: SeedMutator) -> None:
    """Threshold and method/reason drift is detected on isolated seed copies."""
    candidate = copy.deepcopy(_load_clippy_seed())
    mutate(candidate)
    assert _clippy_seed_issues(candidate)


def test_dependabot_seed_contains_the_approved_rust_routing() -> None:
    """The template routes root crates and local actions with bounded updates."""
    template = _load_seed(DEPENDABOT_SEED)
    model = _load_seed(LABEL_SEED)
    assert _dependabot_seed_issues(template) == ()

    model_labels = model.get("labels")
    updates = template.get("updates")
    assert isinstance(model_labels, list)
    assert isinstance(updates, list)

    known_labels: set[str] = set()
    for label in model_labels:
        assert isinstance(label, dict)
        name = label.get("name")
        assert isinstance(name, str)
        known_labels.add(name)
    for update in updates:
        assert isinstance(update, dict)
        labels = update.get("labels")
        assert isinstance(labels, list)
        assert set(labels) <= known_labels


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(_change_dependabot_schedule, id="dependabot_schedule"),
        pytest.param(_change_dependabot_limit, id="dependabot_limit"),
        pytest.param(_change_dependabot_directories, id="dependabot_directory"),
        pytest.param(_change_cargo_default_cooldown, id="cargo_default_cooldown"),
        pytest.param(_change_cargo_major_cooldown, id="cargo_major_cooldown"),
        pytest.param(_change_actions_cooldown, id="actions_default_cooldown"),
        pytest.param(_change_dependabot_group_types, id="group_update_types"),
        pytest.param(_change_dependabot_group_patterns, id="group_patterns"),
        pytest.param(_insert_group_after_minor_patch, id="group_order"),
    ],
)
def test_seed_contracts_reject_dependabot_mutations(mutate: SeedMutator) -> None:
    """Schedule, limits, paths, cooldowns and grouping remain mutation-sensitive."""
    candidate = copy.deepcopy(_load_seed(DEPENDABOT_SEED))
    mutate(candidate)
    assert _dependabot_seed_issues(candidate)


def test_rust_toolchain_cooldown_is_not_required_by_this_seed_contract() -> None:
    """No unapproved cooldown policy is imposed on toolchain updates."""
    candidate = copy.deepcopy(_load_seed(DEPENDABOT_SEED))
    toolchain_update = _update_for(candidate, "rust-toolchain")
    toolchain_update["cooldown"] = {"default-days": 3}
    assert _dependabot_seed_issues(candidate) == ()


def test_label_seed_carries_published_provenance_and_github_bounds() -> None:
    """The label snapshot retains its source, approval and API-safe records."""
    assert _label_seed_issues(_load_seed(LABEL_SEED)) == ()


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(_remove_label_approval, id="missing_approval_reference"),
        pytest.param(_change_label_hash, id="changed_valid_snapshot_hash"),
        pytest.param(_change_label_capture_time, id="changed_valid_capture_date"),
        pytest.param(_drop_label, id="drop_snapshot_label"),
        pytest.param(_overrun_label_name, id="github_label_name_limit"),
        pytest.param(_duplicate_label_name, id="duplicate_label_name"),
    ],
)
def test_seed_contracts_reject_label_snapshot_mutations(mutate: SeedMutator) -> None:
    """Provenance, completeness, and GitHub metadata bounds cannot drift."""
    candidate = copy.deepcopy(_load_seed(LABEL_SEED))
    mutate(candidate)
    assert _label_seed_issues(candidate)


def test_label_seed_matches_captured_bug_record(
    captured_bug_label: dict[str, object],
) -> None:
    """The unchanged seed's bug fields match the independent API capture."""
    baseline = _load_seed(LABEL_SEED)
    assert _label_seed_issues(baseline) == ()
    baseline_bug = _bug_label_record(baseline)
    fields = ("name", "color", "description")
    assert {field: baseline_bug[field] for field in fields} == {
        field: captured_bug_label[field] for field in fields
    }


def test_label_seed_matches_raw_capture_hash_and_content() -> None:
    """The independent capture hashes to and projects into the full oracle."""
    capture_path = (
        Path(__file__).resolve().parents[1]
        / "fixtures"
        / "rust_policy"
        / "netsuke-labels-2026-09-30.json"
    )
    capture_bytes = capture_path.read_bytes()
    assert hashlib.sha256(capture_bytes).hexdigest() == (
        "dad7045b340d12e4393f7e94b61d7aea01073fe7faa17075368de29785f5103a"
    )
    records: object = json.loads(capture_bytes)
    assert isinstance(records, list)

    projection: dict[str, tuple[str, str | None]] = {}
    for record in records:
        assert isinstance(record, dict)
        assert all(isinstance(key, str) for key in record)
        record_fields = typ.cast("dict[str, object]", record)
        assert {"name", "color", "description"} <= set(record_fields)
        name = record_fields["name"]
        color = record_fields["color"]
        description = record_fields["description"]
        assert isinstance(name, str)
        assert 0 < len(name) <= 50
        assert name not in projection
        assert isinstance(color, str)
        assert re.fullmatch(r"[0-9a-fA-F]{6}", color)
        assert description is None or isinstance(description, str)
        assert description is None or len(description) <= 100
        projection[name] = (color, description)

    assert len(projection) == 31
    assert projection == _EXPECTED_LABEL_CONTENT
    assert _label_seed_issues(_load_seed(LABEL_SEED)) == ()


@pytest.mark.parametrize(
    ("field", "replacement", "mutate"),
    [
        pytest.param(
            "color",
            "d73a4b",
            _change_bug_label_color,
            id="bug_color_drift",
        ),
        pytest.param(
            "description",
            "A confirmed defect is not working as intended.",
            _change_bug_label_description,
            id="bug_description_drift",
        ),
    ],
)
def test_seed_contracts_reject_valid_bug_field_drift(
    captured_bug_label: dict[str, object],
    field: typ.Literal["color", "description"],
    replacement: str,
    mutate: SeedMutator,
) -> None:
    """Valid single-field bug label changes differ from the captured record."""
    baseline = _load_seed(LABEL_SEED)
    assert _label_seed_issues(baseline) == ()
    baseline_bug = _bug_label_record(baseline)
    fields = ("name", "color", "description")
    assert {field: baseline_bug[field] for field in fields} == {
        field: captured_bug_label[field] for field in fields
    }

    candidate = copy.deepcopy(baseline)
    expected = copy.deepcopy(baseline)
    mutate(candidate)
    _bug_label_record(expected)[field] = replacement
    assert candidate == expected

    changed_value = _bug_label_record(candidate)[field]
    assert isinstance(changed_value, str)
    assert changed_value != captured_bug_label[field]
    if field == "color":
        assert re.fullmatch(r"[0-9a-f]{6}", changed_value)
    else:
        assert 0 < len(changed_value) < 100
    assert _label_seed_issues(candidate)


@pytest.mark.parametrize(
    ("seed", "exception_type", "message"),
    [
        pytest.param(
            {"updates": None},
            TypeError,
            "Dependabot updates must be a list",
            id="update_list_wrong_type",
        ),
        pytest.param(
            {"updates": []},
            AssertionError,
            "Expected one cargo update entry",
            id="update_entry_missing",
        ),
    ],
)
def test_update_for_preserves_type_and_invariant_errors(
    seed: Seed, exception_type: type[Exception], message: str
) -> None:
    """Invalid update types and missing canonical entries remain distinct."""
    with pytest.raises(exception_type, match=rf"\A{re.escape(message)}\Z"):
        _update_for(copy.deepcopy(seed), "cargo")


@pytest.mark.parametrize(
    ("mutate", "seed", "exception_type", "message"),
    [
        pytest.param(
            _remove_clippy_method,
            {"disallowed-methods": None},
            TypeError,
            "Clippy disallowed-methods must be a list",
            id="clippy_methods_wrong_type",
        ),
        pytest.param(
            _change_clippy_reason,
            {"disallowed-methods": []},
            AssertionError,
            "Expected std::env::var in the shipped seed",
            id="clippy_method_missing",
        ),
        pytest.param(
            _change_dependabot_group_types,
            {"updates": [{"package-ecosystem": "cargo", "groups": []}]},
            TypeError,
            "Expected minor-and-patch group in the shipped seed",
            id="dependabot_groups_wrong_type",
        ),
        pytest.param(
            _change_dependabot_group_types,
            {"updates": [{"package-ecosystem": "cargo"}]},
            AssertionError,
            "Expected minor-and-patch group in the shipped seed",
            id="dependabot_groups_missing",
        ),
        pytest.param(
            _change_label_hash,
            {"source": []},
            TypeError,
            "Expected source provenance in the shipped seed",
            id="label_source_wrong_type",
        ),
        pytest.param(
            _change_label_hash,
            {},
            AssertionError,
            "Expected source provenance in the shipped seed",
            id="label_source_missing",
        ),
        pytest.param(
            _overrun_label_name,
            {"labels": "invalid"},
            TypeError,
            "Expected label records in the shipped seed",
            id="label_records_wrong_type",
        ),
        pytest.param(
            _overrun_label_name,
            {"labels": []},
            AssertionError,
            "Expected label records in the shipped seed",
            id="label_records_missing",
        ),
    ],
)
def test_seed_mutators_preserve_type_and_invariant_errors(
    mutate: SeedMutator,
    seed: Seed,
    exception_type: type[Exception],
    message: str,
) -> None:
    """Mutation setup type errors differ from missing-seed invariants."""
    with pytest.raises(exception_type, match=rf"\A{re.escape(message)}\Z"):
        mutate(copy.deepcopy(seed))
