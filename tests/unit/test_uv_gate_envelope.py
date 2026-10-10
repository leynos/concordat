"""Specify the uv-gate-baseline policy-input envelope."""

from __future__ import annotations

import hashlib
import pathlib
import typing as typ

import pytest

from concordat.errors import OperationalRuleError
from concordat.rules import packages
from concordat.rules.uv_gate_envelope import (
    ENVELOPE_KIND,
    ENVELOPE_SCHEMA_VERSION,
    build_uv_gate_envelope,
)

RULE_ID: typ.Final = "uv-gate-baseline"
SHA: typ.Final = "0123456789abcdef0123456789abcdef01234567"


def _write(checkout: pathlib.Path, relative: str, text: str) -> pathlib.Path:
    """Write *text* at *relative* under *checkout* and return the path."""
    path = checkout / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_an_empty_checkout_carries_no_facts(tmp_path: pathlib.Path) -> None:
    """Nothing to read is recorded as absence, never as a decoding error."""
    envelope = build_uv_gate_envelope(tmp_path)

    assert envelope["schema_version"] == ENVELOPE_SCHEMA_VERSION
    assert envelope["kind"] == ENVELOPE_KIND
    assert envelope["makefile"] is None
    assert envelope["gate"] is None
    assert envelope["pyproject"] is None
    assert envelope["workflows"] == []
    assert envelope["actions"] == []
    assert envelope["requirements"] == []
    assert envelope["git_sources"] == []
    assert envelope["applicability"] == {
        "root_makefile": False,
        "pyproject": False,
        "uv_lock": False,
        "gate_file": False,
    }


def test_the_helper_is_recorded_as_the_digest_of_its_bytes(
    tmp_path: pathlib.Path,
) -> None:
    """A re-encoded copy differs, so the digest is of the bytes on disk."""
    data = b"# helper\r\nprint('x')\n"
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "uv_gate.py").write_bytes(data)

    envelope = build_uv_gate_envelope(tmp_path)

    assert envelope["gate"] == {
        "path": "scripts/uv_gate.py",
        "sha256": hashlib.sha256(data).hexdigest(),
        "error": None,
    }
    assert envelope["applicability"]["gate_file"] is True


def test_the_lock_is_noted_beside_the_project(tmp_path: pathlib.Path) -> None:
    """pyproject.toml and uv.lock are separate applicability facts."""
    _write(tmp_path, "pyproject.toml", "[project]\nname = 'x'\n")
    _write(tmp_path, "uv.lock", "version = 1\n")

    applicability = build_uv_gate_envelope(tmp_path)["applicability"]

    assert applicability["pyproject"] is True
    assert applicability["uv_lock"] is True


def test_requirements_come_from_every_table_that_declares_them(
    tmp_path: pathlib.Path,
) -> None:
    """Dependencies, extras, groups, build requirements and legacy dev lists."""
    _write(
        tmp_path,
        "pyproject.toml",
        """\
[project]
name = "x"
dependencies = ["a>=1"]
[project.optional-dependencies]
extra = ["b==2"]
[dependency-groups]
dev = ["c", {include-group = "other"}]
[build-system]
requires = ["hatchling"]
[tool.uv]
dev-dependencies = ["d"]
""",
    )

    pairs = [
        (item["origin"], item["spec"])
        for item in build_uv_gate_envelope(tmp_path)["requirements"]
    ]

    assert pairs == [
        ("project.dependencies", "a>=1"),
        ("project.optional-dependencies.extra", "b==2"),
        ("dependency-groups.dev", "c"),
        ("build-system.requires", "hatchling"),
        ("tool.uv.dev-dependencies", "d"),
    ]


def test_git_sources_keep_every_revision_selector(tmp_path: pathlib.Path) -> None:
    """A source may carry rev, tag or branch, and may be a list by marker."""
    _write(
        tmp_path,
        "pyproject.toml",
        f"""\
[tool.uv.sources]
a = {{ git = "https://example.invalid/a.git", rev = "{SHA}" }}
b = [{{ git = "https://example.invalid/b.git", tag = "v1" }}, {{ path = "../b" }}]
c = {{ git = "https://example.invalid/c.git", branch = "main" }}
d = {{ workspace = true }}
""",
    )

    sources = build_uv_gate_envelope(tmp_path)["git_sources"]

    assert sources == [
        {
            "name": "a",
            "git": "https://example.invalid/a.git",
            "rev": SHA,
            "tag": None,
            "branch": None,
        },
        {
            "name": "b",
            "git": "https://example.invalid/b.git",
            "rev": None,
            "tag": "v1",
            "branch": None,
        },
        {
            "name": "c",
            "git": "https://example.invalid/c.git",
            "rev": None,
            "tag": None,
            "branch": "main",
        },
    ]


@pytest.mark.parametrize(
    ("content", "reason"),
    [(b"[project\n", "invalid TOML"), (b"\xff\xfe", "not UTF-8")],
    ids=["invalid TOML", "not UTF-8"],
)
def test_an_undecodable_pyproject_keeps_its_reason(
    tmp_path: pathlib.Path, content: bytes, reason: str
) -> None:
    """A content failure is evidence for an indeterminate verdict, not absence."""
    (tmp_path / "pyproject.toml").write_bytes(content)

    envelope = build_uv_gate_envelope(tmp_path)

    assert envelope["pyproject"] is not None
    assert envelope["pyproject"]["parsed"] is None
    assert reason in str(envelope["pyproject"]["error"])
    assert envelope["requirements"] == []


def test_actions_are_ordered_whatever_order_the_filesystem_lists_them(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Directory listing order is arbitrary, so the envelope sorts it."""
    for name in ("a", "b", "c"):
        _write(tmp_path, f".github/actions/{name}/action.yml", f"name: {name}\n")
    real_iterdir = pathlib.Path.iterdir
    monkeypatch.setattr(
        pathlib.Path,
        "iterdir",
        lambda self: iter(sorted(real_iterdir(self), reverse=True)),
    )

    envelope = build_uv_gate_envelope(tmp_path)

    assert [item["path"] for item in envelope["actions"]] == [
        ".github/actions/a/action.yml",
        ".github/actions/b/action.yml",
        ".github/actions/c/action.yml",
    ]


def test_workflows_and_composite_actions_are_both_decoded(
    tmp_path: pathlib.Path,
) -> None:
    """The cache variables can be set in either, so both are read."""
    _write(tmp_path, ".github/workflows/ci.yml", "name: ci\non: push\njobs: {}\n")
    _write(
        tmp_path, ".github/actions/b/action.yml", "name: b\nruns: {using: composite}\n"
    )
    _write(
        tmp_path, ".github/actions/a/action.yaml", "name: a\nruns: {using: composite}\n"
    )
    _write(tmp_path, ".github/actions/c/README.md", "no action file here\n")

    envelope = build_uv_gate_envelope(tmp_path)

    assert [item["path"] for item in envelope["workflows"]] == [
        ".github/workflows/ci.yml"
    ]
    assert [item["path"] for item in envelope["actions"]] == [
        ".github/actions/a/action.yaml",
        ".github/actions/b/action.yml",
    ]


def test_an_undecodable_action_keeps_its_error(tmp_path: pathlib.Path) -> None:
    """An action that cannot be decoded is carried, not dropped."""
    _write(tmp_path, ".github/actions/a/action.yml", "runs: [unterminated\n")

    [action] = build_uv_gate_envelope(tmp_path)["actions"]

    assert action["parsed"] is None
    assert action["error"]


def test_a_gate_outside_the_checkout_is_refused(tmp_path: pathlib.Path) -> None:
    """No policy input is read from outside the checkout."""
    checkout = tmp_path / "checkout"
    (checkout / "scripts").mkdir(parents=True)
    outside = tmp_path / "secret.py"
    outside.write_text("secret\n", encoding="utf-8")
    (checkout / "scripts" / "uv_gate.py").symlink_to(outside)

    with pytest.raises(OperationalRuleError):
        build_uv_gate_envelope(checkout)


def test_the_package_resolves_to_this_envelope(tmp_path: pathlib.Path) -> None:
    """The registry names this builder for the package and for its kind."""
    builder = packages.PACKAGE_ENVELOPE_BUILDERS[RULE_ID]

    assert builder is packages.INPUT_KIND_ENVELOPE_BUILDERS[ENVELOPE_KIND]
    assert builder(tmp_path)["kind"] == ENVELOPE_KIND
