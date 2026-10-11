"""Specify the recorded clippy-gate exception facts of the documentation envelope."""

from __future__ import annotations

import typing as typ

import pytest
from hypothesis import given
from hypothesis import strategies as st

from concordat.errors import OperationalRuleError
from concordat.rules.clippy_exception import (
    EXCEPTION_KEYWORD,
    _candidate_paths,
    clippy_exceptions,
)
from concordat.rules.documentation_envelope import build_documentation_envelope
from concordat.rules.exception_docs import sections_in_text
from concordat.rules.rust_clippy import invokes_script

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
        build_documentation_envelope(root)["clippy_exceptions"]["records"],
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


def test_a_section_is_found_by_a_case_insensitive_heading_keyword() -> None:
    """The keyword matches anywhere in a heading, ignoring case."""
    text = "# Guide\n\n### The clippy GATE exception\n\nBody `a.sh`.\n"

    (section,) = sections_in_text(text, EXCEPTION_KEYWORD)

    assert section[0] == "The clippy GATE exception"
    assert "`a.sh`" in section[2]


def test_a_heading_inside_a_fenced_block_is_not_a_section() -> None:
    """A fenced example of a heading does not record an exception."""
    text = "# Guide\n\n```md\n## Clippy gate\n```\n"

    assert sections_in_text(text, EXCEPTION_KEYWORD) == []


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
        'CARGO = "cargo"\nARGV = ["clippy", "--workspace"]\n',
        "subprocess.run(['cargo', 'clippy'])\n",
        "cargo --locked clippy --all\n",
        "RUSTFLAGS=-Dwarnings cargo clippy\n",
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

    assert clippy_exceptions(tmp_path, tmp_path.resolve(), []) == {
        "records": [],
        "errors": [],
    }


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("\tsh scripts/lanes.sh\n", True),
        ("\t./scripts/lanes.sh --all\n", True),
        ("\tscripts/lanes.sh\n", True),
        ("\tbash -e scripts/lanes.sh\n", True),
        ("\tuv run --script scripts/lanes.sh\n", True),
        ("\tFOO=1 sh scripts/lanes.sh && echo done\n", True),
        ("\techo scripts/lanes.sh\n", False),
        ("\tcat scripts/lanes.sh\n", False),
        ("\ttest -x scripts/lanes.sh\n", False),
        ("\tsh scripts/other.sh\n", False),
        ("\tmake scripts/lanes.sh\n", False),
    ],
)
def test_a_script_is_reached_only_when_a_command_runs_it(
    line: str, *, expected: bool
) -> None:
    """Naming the script in `echo` or `cat` is not running it."""
    assert invokes_script(line.strip(), "scripts/lanes.sh") is expected


def test_a_mentioned_but_not_run_script_is_not_reached(tmp_path: pathlib.Path) -> None:
    """The exception cannot be satisfied by a recipe that only prints the path."""
    _write(tmp_path, "Makefile", ".PHONY: lint\nlint:\n\techo scripts/lanes.sh\n")
    _write(tmp_path, "scripts/lanes.sh", "cargo clippy\n")
    _write(tmp_path, GUIDE, "## Clippy gate\n\n`scripts/lanes.sh`\n")

    (exception,) = _exceptions(tmp_path)

    assert exception["scripts"][0]["reached"] is False


def test_a_guide_that_is_a_link_to_a_file_outside_the_checkout_is_refused(
    tmp_path: pathlib.Path,
) -> None:
    """The exception record cannot be imported from outside the checkout."""
    outside = tmp_path / "outside.md"
    outside.write_text("## Clippy gate\n\n`scripts/lanes.sh`\n", encoding="utf-8")
    checkout = tmp_path / "repo"
    _write(checkout, "Makefile", LINT_MAKEFILE)
    _write(checkout, "scripts/lanes.sh", "cargo clippy\n")
    (checkout / "docs").mkdir()
    (checkout / GUIDE).symlink_to(outside)

    with pytest.raises(OperationalRuleError, match="outside the checkout"):
        _exceptions(checkout)


def test_a_guide_that_is_not_utf8_is_an_error_not_an_absence(
    tmp_path: pathlib.Path,
) -> None:
    """An unreadable guide must not read as "no exception recorded"."""
    (tmp_path / "docs").mkdir()
    (tmp_path / GUIDE).write_bytes(b"\xff\xfe## Clippy gate")

    result = build_documentation_envelope(tmp_path)["clippy_exceptions"]

    assert result["records"] == []
    assert len(result["errors"]) == 1
    assert result["errors"][0].startswith(f"{GUIDE}: not UTF-8 text")


def test_an_absent_guide_is_neither_a_record_nor_an_error(
    tmp_path: pathlib.Path,
) -> None:
    """No guide means no exception was recorded, which is an answer."""
    assert build_documentation_envelope(tmp_path)["clippy_exceptions"] == {
        "records": [],
        "errors": [],
    }


_HEADINGS: typ.Final = st.sampled_from([
    "## Clippy gate exception\n",
    "### the CLIPPY GATE\n",
    "## Linting\n",
    "# Guide\n",
    "Some prose.\n",
])


@given(st.lists(st.tuples(_HEADINGS, st.booleans()), max_size=10))
def test_only_matching_headings_outside_fences_are_sections(
    pieces: list[tuple[str, bool]],
) -> None:
    """A heading counts when it names the keyword and sits outside a fence."""
    text = "".join(f"```\n{h}```\n" if fenced else h for h, fenced in pieces)
    expected = sum(
        1 for h, fenced in pieces if not fenced and "clippy gate" in h.casefold()
    )

    assert len(sections_in_text(text, EXCEPTION_KEYWORD)) == expected
