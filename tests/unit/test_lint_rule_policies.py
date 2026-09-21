"""Run every canon lint-rule package's own Rego suite.

Each rule package ships a Conftest/Rego test file beside its policy, and those
tests are where a clause's semantics are actually pinned. Until this module
existed nothing ran them: `make test` collected only Python, and the
continuous-integration policy step covers the OpenTofu policies alone. A
clause proved by a Rego test that never runs is a clause with no proof.
"""

from __future__ import annotations

import pathlib
import subprocess
import typing as typ

import pytest

RULE_PACKAGES_DIR: typ.Final = (
    pathlib.Path(__file__).resolve().parents[2]
    / "platform-standards"
    / "canon"
    / "lint-rules"
)

CONFTEST_TIMEOUT: typ.Final = 120.0


def rule_packages() -> list[pathlib.Path]:
    """Return every rule package directory that ships a policy.

    Returns
    -------
    list[pathlib.Path]
        The rule package directories, sorted by name.
    """
    return sorted(
        path for path in RULE_PACKAGES_DIR.iterdir() if (path / "policy").is_dir()
    )


def test_the_discovery_finds_the_shipped_packages() -> None:
    """A verification parametrised over an empty list would pass vacuously."""
    names = [package.name for package in rule_packages()]
    assert "rust-makefile-baseline" in names
    assert "rust-build-defaults" in names


@pytest.mark.parametrize("package", rule_packages(), ids=lambda package: package.name)
def test_the_package_policy_suite_passes(package: pathlib.Path) -> None:
    """`conftest verify` runs the package's Rego tests against its fixtures."""
    completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [  # noqa: S607 - resolved from PATH, as the runner resolves it
            "conftest",
            "verify",
            "--policy",
            str(package / "policy"),
            "--data",
            str(package / "fixtures" / "data.json"),
        ],
        capture_output=True,
        text=True,
        timeout=CONFTEST_TIMEOUT,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
