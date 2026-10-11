"""Specify the recorded clippy-gate exception facts of the documentation envelope."""

from __future__ import annotations

import typing as typ

import pytest

from concordat.rules.clippy_exception import (
    EXCEPTION_KEYWORD,
    _candidate_paths,
    clippy_exceptions,
)
from concordat.rules.documentation_envelope import build_documentation_envelope
from concordat.rules.exception_docs import section_bodies

if typ.TYPE_CHECKING:
    import pathlib

GUIDE = "docs/developers-guide.md"
LINT_MAKEFILE: typ.Final = ".PHONY: lint\nlint:\n\tsh scripts/lanes.sh\n"


def _write(root: pathlib.Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _exceptions(root: pathlib.Path) -> list[dict[str, typ.Any]]:
    return typ.cast(
        "list[dict[str, typ.Any]]",
        build_documentation_envelope(root)["clippy_exceptions"],
    )


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("Runs `scripts/lanes.sh` and `scripts/lanes.sh` again.", ["scripts/lanes.sh"]),
        ("Uses `lanes.py` and `make lint`.", ["lanes.py"]),
        ("Not a path: `cargo clippy` and `--flag`.", []),
        ("Absolute `/usr/bin/x.sh` is refused.", []),
        ("Nothing in backticks scripts/lanes.sh.", []),
    ],
)
def test_script_candidates_are_the_backticked_paths(
    body: str, expected: list[str]
) -> None:
    """Only backticked tokens that look like script paths are candidates."""
    assert _candidate_paths(body) == expected


def test_a_section_is_found_by_a_case_insensitive_heading_keyword(
    tmp_path: pathlib.Path,
) -> None:
    """The keyword matches anywhere in a heading, ignoring case."""
    _write(
        tmp_path, GUIDE, "# Guide\n\n### The clippy GATE exception\n\nBody `a.sh`.\n"
    )

    (section,) = section_bodies(tmp_path, GUIDE, EXCEPTION_KEYWORD)

    assert section[0] == "The clippy GATE exception"
    assert "`a.sh`" in section[2]


def test_a_heading_inside_a_fenced_block_is_not_a_section(
    tmp_path: pathlib.Path,
) -> None:
    """A fenced example of a heading does not record an exception."""
    _write(tmp_path, GUIDE, "# Guide\n\n```md\n## Clippy gate\n```\n")

    assert section_bodies(tmp_path, GUIDE, EXCEPTION_KEYWORD) == []


def test_the_exception_facts_record_existence_clippy_and_reach(
    tmp_path: pathlib.Path,
) -> None:
    """Each named script is checked on disk, for clippy, and against `lint`."""
    _write(tmp_path, "Makefile", LINT_MAKEFILE)
    _write(tmp_path, "scripts/lanes.sh", "cargo +nightly clippy -- -D warnings\n")
    _write(tmp_path, "scripts/quiet.sh", "cargo fmt --check\n")
    _write(
        tmp_path,
        GUIDE,
        "## Clippy gate\n\nUses `scripts/lanes.sh`, `scripts/quiet.sh` and "
        "`scripts/gone.sh`.\n",
    )

    (exception,) = _exceptions(tmp_path)
    scripts = {item["path"]: item for item in exception["scripts"]}

    assert scripts["scripts/lanes.sh"] == {
        "path": "scripts/lanes.sh",
        "exists": True,
        "runs_clippy": True,
        "reached": True,
    }
    assert scripts["scripts/quiet.sh"]["runs_clippy"] is False
    assert scripts["scripts/quiet.sh"]["reached"] is False
    assert scripts["scripts/gone.sh"]["exists"] is False


@pytest.mark.parametrize(
    "source",
    [
        'ARGV = ["clippy", "--workspace"]\n',
        "subprocess.run(['cargo', 'clippy'])\n",
        "cargo clippy --all\n",
        "cargo +nightly clippy\n",
    ],
)
def test_a_script_runs_clippy_when_it_names_it_as_a_cargo_subcommand(
    tmp_path: pathlib.Path, source: str
) -> None:
    """Written out, or handed to cargo as an argument."""
    _write(tmp_path, "Makefile", LINT_MAKEFILE)
    _write(tmp_path, "scripts/lanes.sh", source)
    _write(tmp_path, GUIDE, "## Clippy gate\n\n`scripts/lanes.sh`\n")

    (exception,) = _exceptions(tmp_path)

    assert exception["scripts"][0]["runs_clippy"] is True


@pytest.mark.parametrize(
    "source", ["clippy_utils = 1\n", "touch clippy\n", "echo done\n"]
)
def test_a_script_that_only_mentions_the_word_does_not_run_clippy(
    tmp_path: pathlib.Path, source: str
) -> None:
    """A bare mention is not a subcommand."""
    _write(tmp_path, "Makefile", LINT_MAKEFILE)
    _write(tmp_path, "scripts/lanes.sh", source)
    _write(tmp_path, GUIDE, "## Clippy gate\n\n`scripts/lanes.sh`\n")

    (exception,) = _exceptions(tmp_path)

    assert exception["scripts"][0]["runs_clippy"] is False


def test_a_path_that_leaves_the_checkout_does_not_exist_for_the_audit(
    tmp_path: pathlib.Path,
) -> None:
    """The record cannot point the audit at a file outside the checkout."""
    checkout = tmp_path / "repo"
    _write(tmp_path, "outside.sh", "cargo clippy\n")
    _write(checkout, "Makefile", ".PHONY: lint\nlint:\n\tsh ../outside.sh\n")
    _write(checkout, GUIDE, "## Clippy gate\n\n`../outside.sh`\n")

    (exception,) = _exceptions(checkout)

    assert exception["scripts"][0]["exists"] is False


def test_no_section_means_no_exception(tmp_path: pathlib.Path) -> None:
    """A guide without the heading records nothing."""
    _write(tmp_path, GUIDE, "# Guide\n\n## Linting\n\nNothing special.\n")

    assert clippy_exceptions(tmp_path, tmp_path.resolve(), []) == []
