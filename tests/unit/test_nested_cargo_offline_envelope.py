"""Assemble the nested-cargo offline evidence for one checkout.

The envelope is the join of three readers, and its value to the policy is that
none of them is allowed to round an unknown up to a clean answer. These tests
therefore check the shape the policy reads — the kind, the repository slug, and
one list each of spawns, trybuild evidence, entry points and problems — and
that a fact the readers could not establish survives as a problem rather than
as silence.
"""

from __future__ import annotations

import pathlib

import pygit2

from concordat.rules.nested_cargo_offline_envelope import (
    ENVELOPE_KIND,
    ENVELOPE_SCHEMA_VERSION,
    build_nested_cargo_offline_envelope,
)

SPAWN = 'fn run() {\n    Command::new("cargo").arg("build").status();\n}\n'
MAKEFILE = "Makefile"
WORKFLOW = ".github/workflows/ci.yml"


def put(root: pathlib.Path, relative: str, text: str) -> None:
    """Write one file, creating its directory."""
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_the_envelope_names_itself_and_its_schema() -> None:
    """The policy refuses a document whose kind or version it does not know."""
    envelope = build_nested_cargo_offline_envelope(pathlib.Path())
    assert envelope["kind"] == ENVELOPE_KIND
    assert envelope["schema_version"] == ENVELOPE_SCHEMA_VERSION


def test_a_checkout_with_no_repository_has_no_slug(tmp_path: pathlib.Path) -> None:
    """A local directory has no origin, so no exemption can name it."""
    envelope = build_nested_cargo_offline_envelope(tmp_path)
    assert envelope["repository"] == {"path": ".", "name": None}


def test_the_origin_slug_names_the_repository(tmp_path: pathlib.Path) -> None:
    """The exemptions are matched on `owner/name`, so the slug must survive."""
    pygit2.init_repository(str(tmp_path))
    repository = pygit2.Repository(str(tmp_path / ".git"))
    repository.remotes.create("origin", "git@github.com:leynos/whitaker.git")
    envelope = build_nested_cargo_offline_envelope(tmp_path)
    assert envelope["repository"] == {"path": ".", "name": "leynos/whitaker"}


def test_the_envelope_carries_all_four_fact_lists(tmp_path: pathlib.Path) -> None:
    """Every reader's output reaches the policy; a dropped list is a silent gap."""
    put(tmp_path, "tests/spawn.rs", SPAWN)
    put(tmp_path, "tests/ui.rs", "fn main() {\n    trybuild::TestCases::new();\n}\n")
    put(tmp_path, MAKEFILE, "test:\n\tcargo test\n")
    put(
        tmp_path,
        WORKFLOW,
        "jobs:\n  test:\n    steps:\n      - run: cargo test\n",
    )
    envelope = build_nested_cargo_offline_envelope(tmp_path)
    assert [(fact["form"], fact["state"]) for fact in envelope["spawns"]] == [
        ("command_new", "online")
    ]
    assert [fact["path"] for fact in envelope["trybuild"]] == ["tests/ui.rs"]
    assert [(fact["kind"], fact["name"]) for fact in envelope["entry_points"]] == [
        ("makefile", "test"),
        ("workflow", "test"),
    ]
    assert envelope["problems"] == []


def test_an_unreadable_input_survives_as_a_problem(tmp_path: pathlib.Path) -> None:
    """An unparseable Makefile is neither an entry point nor a clean absence."""
    put(tmp_path, MAKEFILE, "test:\n\tcargo test\nendif\n")
    envelope = build_nested_cargo_offline_envelope(tmp_path)
    assert envelope["entry_points"] == []
    assert any(MAKEFILE in problem for problem in envelope["problems"])


def test_parameters_are_ignored(tmp_path: pathlib.Path) -> None:
    """Exemptions and severity are the policy's, so the builder must not react."""
    parameters = {
        "exemptions": [
            {
                "repository": "leynos/whitaker",
                "path": "tests/spawn.rs",
                "rationale": "the fixture is the system under test",
                "review_trigger": "when the fixture gains a lockfile",
            }
        ]
    }
    envelope = build_nested_cargo_offline_envelope(tmp_path, parameters)
    assert envelope["spawns"] == []
    assert envelope["repository"]["path"] == "."
