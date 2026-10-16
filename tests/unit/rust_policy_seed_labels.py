"""Validate parsed seed mappings and the published label snapshot contract."""

from __future__ import annotations

import datetime as dt
import re
import typing as typ

_SOURCE_FIELDS = {
    "repository",
    "api",
    "observed_at",
    "capture_command",
    "captured_json_sha256",
    "approval_reference",
}
_EXPECTED_SOURCE_VALUES = {
    "repository": "leynos/netsuke",
    "api": "https://api.github.com/repos/leynos/netsuke/labels",
    "capture_command": "gh api repos/leynos/netsuke/labels --paginate",
    "approval_reference": "leynos/concordat#157",
}
_EXPECTED_LABEL_CONTENT: dict[str, tuple[str, str | None]] = {
    "bug": ("d73a4a", "Something isn't working"),
    "bun": ("ededed", "Dependabot channel for Bun dependency updates."),
    "cargo": ("ededed", "Dependabot channel for Rust crate updates."),
    "codex": ("ededed", None),
    "concurrency": (
        "5319e7",
        (
            "Concurrency, parallelism, and synchronization work, "
            "including races and deadlocks."
        ),
    ),
    "critical": (
        "b60205",
        (
            "Blocker-level work such as active incidents or production outages. "
            "Pre-empts all other work."
        ),
    ),
    "dependencies": (
        "0366d6",
        "Dependency updates, applied to every Dependabot pull request.",
    ),
    "documentation": (
        "0075ca",
        "Improvements or additions to documentation",
    ),
    "duplicate": ("cfd3d7", "This issue or pull request already exists"),
    "enhancement": ("a2eeef", "New feature or request"),
    "epic": ("2da1a3", "Too much excitement for a little cat"),
    "github-actions": (
        "ededed",
        (
            "Dependabot channel for GitHub Actions updates, batched into one "
            "pull request per run."
        ),
    ),
    "go": ("ededed", "Dependabot channel for Go module updates."),
    "good first issue": ("7057ff", "Good for newcomers"),
    "help wanted": ("008672", "Extra attention is needed"),
    "high": (
        "d93f0b",
        (
            "Unblocks delivery within the current sprint or release window. "
            "Reaction time is days, not hours."
        ),
    ),
    "invalid": ("e4e669", "This doesn't seem right"),
    "Issue": ("ededed", "A pull request originating from an issue"),
    "low": (
        "0e8a16",
        (
            "Backlog work, hygiene, or technical debt planned opportunistically, "
            "without a strict deadline."
        ),
    ),
    "medium": (
        "fbca04",
        (
            "Roadmap items to schedule within the current quarter. Clear scope, "
            "normal review cycles."
        ),
    ),
    "opentofu": (
        "ededed",
        "Dependabot channel for OpenTofu provider and module updates.",
    ),
    "performance": (
        "ededed",
        (
            "Bugs or prior decisions disproportionately impacting memory, time, "
            "storage or CPU usage"
        ),
    ),
    "pip": ("ededed", "Dependabot channel for Python dependency updates via pip."),
    "question": ("d876e3", "Further information is requested"),
    "refactor": (
        "a2eeef",
        "Behaviour-preserving restructuring that improves code health.",
    ),
    "Roadmap": ("ededed", "A pull request originating from a roadmap item"),
    "rust-toolchain": (
        "ededed",
        "Dependabot channel for Rust toolchain pin updates.",
    ),
    "testing": (
        "1d76db",
        "Test coverage, test infrastructure, and verification tooling work.",
    ),
    "uv": ("ededed", "Dependabot channel for Python dependency updates via uv."),
    "Windows": ("ededed", None),
    "wontfix": ("ffffff", "This will not be worked on"),
}
_EXPECTED_LABEL_NAMES = frozenset(_EXPECTED_LABEL_CONTENT)
_EXPECTED_CAPTURE_TIME = "2026-09-30T11:38:30Z"
_EXPECTED_CAPTURE_HASH = (
    "dad7045b340d12e4393f7e94b61d7aea01073fe7faa17075368de29785f5103a"
)

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
    if set(document) != {"schema_version", "source", "labels"}:
        return ("label snapshot must use the published top-level shape",)
    if (
        type(document.get("schema_version")) is not int
        or document.get("schema_version") != 1
    ):
        return ("label snapshot schema_version must be 1",)

    issues = _label_source_issues(document.get("source"))
    labels_value = document.get("labels")
    if not isinstance(labels_value, list):
        issues.append("labels must be a list")
        return tuple(issues)
    names: list[str] = []
    for label_value in labels_value:
        record_issues, name = _label_record_issues(label_value)
        issues.extend(record_issues)
        if name is not None:
            names.append(name)
    issues.extend(_label_snapshot_name_issues(names))
    return tuple(issues)


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
    return issues


def _label_source_identity_issues(source: Seed) -> list[str]:
    """Require the exact repository, API, command, and approval reference."""
    issues: list[str] = []
    for key, expected in _EXPECTED_SOURCE_VALUES.items():
        if source.get(key) != expected:
            issues.append(f"source {key} must match the approved snapshot")
    return issues


def _label_capture_time_issues(observed_at: object) -> list[str]:
    """Require a valid UTC timestamp equal to the published capture time."""
    parsed_time = None
    if isinstance(observed_at, str):
        try:
            parsed_time = dt.datetime.fromisoformat(observed_at)
        except ValueError:
            parsed_time = None

    issues: list[str] = []
    if parsed_time is None or parsed_time.utcoffset() != dt.timedelta(0):
        issues.append("source observed_at must be a UTC timestamp")
    if observed_at != _EXPECTED_CAPTURE_TIME:
        issues.append("source observed_at must match the approved snapshot")
    return issues


def _label_capture_hash_issues(captured_hash: object) -> list[str]:
    """Require a lowercase SHA-256 matching the published snapshot."""
    issues: list[str] = []
    if (
        not isinstance(captured_hash, str)
        or re.fullmatch(r"[0-9a-f]{64}", captured_hash) is None
    ):
        issues.append("source captured_json_sha256 must be a lowercase SHA-256")
    if captured_hash != _EXPECTED_CAPTURE_HASH:
        issues.append("source captured_json_sha256 must match the approved snapshot")
    return issues


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
    """Require known names to retain their captured colour and description."""
    expected_content = _EXPECTED_LABEL_CONTENT.get(name)
    if expected_content is None:
        return []

    expected_color, expected_description = expected_content
    issues: list[str] = []
    if label.get("color") != expected_color:
        issues.append(f"label {name} colour must match the approved snapshot")
    if label.get("description") != expected_description:
        issues.append(f"label {name} description must match the approved snapshot")
    return issues


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
