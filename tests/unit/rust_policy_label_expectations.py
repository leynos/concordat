"""Independent maintainer-approved policy values, not the missing raw API capture."""

from __future__ import annotations

# Reviewed projected values were approved on 2026-10-16. The last three labels
# were requested afterwards; none is asserted to occur in the 31-label capture.
APPROVED_LABELS: dict[str, tuple[str, str | None]] = {
    "Issue": ("ededed", "A pull request originating from an issue"),
    "Roadmap": ("ededed", "A pull request originating from a roadmap item"),
    "Windows": ("ededed", None),
    "bug": ("d73a4a", "Something isn't working"),
    "bun": ("ededed", "Dependabot channel for Bun dependency updates."),
    "cargo": ("ededed", "Dependabot channel for Rust crate updates."),
    "codex": ("ededed", None),
    "concurrency": (
        "5319e7",
        (
            "Concurrency, parallelism, and synchronization work, including "
            "races and deadlocks."
        ),
    ),
    "critical": (
        "b60205",
        (
            "Blocker-level work such as active incidents or production "
            "outages. Pre-empts all other work."
        ),
    ),
    "dependencies": (
        "0366d6",
        "Dependency updates, applied to every Dependabot pull request.",
    ),
    "documentation": ("0075ca", "Improvements or additions to documentation"),
    "duplicate": ("cfd3d7", "This issue or pull request already exists"),
    "enhancement": ("a2eeef", "New feature or request"),
    "epic": ("2da1a3", "Too much excitement for a little cat"),
    "github-actions": (
        "ededed",
        (
            "Dependabot channel for GitHub Actions updates, batched into "
            "one pull request per run."
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
    "infra": (
        "5319e7",
        (
            "stuff that the user doesn't care about, but will hate us for "
            "when their app doesn't land"
        ),
    ),
    "invalid": ("e4e669", "This doesn't seem right"),
    "low": (
        "0e8a16",
        (
            "Backlog work, hygiene, or technical debt planned "
            "opportunistically, without a strict deadline."
        ),
    ),
    "macos": ("ededed", "only breaks in mac land"),
    "medium": (
        "fbca04",
        (
            "Roadmap items to schedule within the current quarter. Clear "
            "scope, normal review cycles."
        ),
    ),
    "opentofu": (
        "ededed",
        "Dependabot channel for OpenTofu provider and module updates.",
    ),
    "performance": (
        "ededed",
        (
            "Bugs or prior decisions disproportionately impacting memory, "
            "time, storage or CPU usage"
        ),
    ),
    "pip": ("ededed", "Dependabot channel for Python dependency updates via pip."),
    "question": ("d876e3", "Further information is requested"),
    "refactor": (
        "a2eeef",
        "Behaviour-preserving restructuring that improves code health.",
    ),
    "rust-toolchain": ("ededed", "Dependabot channel for Rust toolchain pin updates."),
    "security": ("b60205", "keep the skiddies out"),
    "testing": (
        "1d76db",
        "Test coverage, test infrastructure, and verification tooling work.",
    ),
    "uv": ("ededed", "Dependabot channel for Python dependency updates via uv."),
    "wontfix": ("ffffff", "This will not be worked on"),
}

ADDED_LABEL_NAMES = ("security", "macos", "infra")
PROJECTION_SHA256 = "19600e430c336d9d4a24acd880e83e31df8fe0d9bab9c3b3eec37b466eed968d"
