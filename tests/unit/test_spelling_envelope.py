"""Specify the spelling-config-baseline policy-input envelope."""

from __future__ import annotations

import typing as typ

import pytest

from concordat.errors import OperationalRuleError
from concordat.rules.spelling_envelope import (
    DEFAULT_VENDORED_PATTERNS,
    ENVELOPE_KIND,
    ENVELOPE_SCHEMA_VERSION,
    build_spelling_envelope,
)

if typ.TYPE_CHECKING:
    import pathlib


def _write(checkout: pathlib.Path, relative: str, text: str) -> pathlib.Path:
    """Write *text* at *relative* under *checkout* and return the path."""
    path = checkout / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_an_empty_checkout_carries_no_facts(tmp_path: pathlib.Path) -> None:
    """Nothing to read is recorded as absence, never as a decoding error."""
    envelope = build_spelling_envelope(tmp_path)

    assert envelope["schema_version"] == ENVELOPE_SCHEMA_VERSION
    assert envelope["kind"] == ENVELOPE_KIND
    assert envelope["makefile"] is None
    assert envelope["typos_local"] is None
    assert envelope["gitignore"] is None
    assert envelope["workflows"] == []
    assert envelope["vendored"] == []
    assert envelope["applicability"] == {
        "root_makefile": False,
        "typos_config": False,
        "typos_local": False,
    }


def test_the_overlay_is_decoded_as_toml(tmp_path: pathlib.Path) -> None:
    """The overlay's tables reach the policy as decoded values."""
    _write(tmp_path, "typos.local.toml", "schema = 1\n[patterns]\nignore = ['x']\n")
    _write(tmp_path, "typos.toml", "[default]\n")

    envelope = build_spelling_envelope(tmp_path)

    assert envelope["typos_local"] == {
        "path": "typos.local.toml",
        "parsed": {"schema": 1, "patterns": {"ignore": ["x"]}},
        "error": None,
    }
    assert envelope["applicability"]["typos_local"] is True
    assert envelope["applicability"]["typos_config"] is True


@pytest.mark.parametrize(
    ("content", "reason"),
    [(b"schema = [\n", "invalid TOML"), (b"\xff\xfe", "not UTF-8")],
    ids=["invalid TOML", "not UTF-8"],
)
def test_an_undecodable_overlay_keeps_its_reason(
    tmp_path: pathlib.Path, content: bytes, reason: str
) -> None:
    """A content failure is evidence for an indeterminate verdict, not absence."""
    (tmp_path / "typos.local.toml").write_bytes(content)

    overlay = build_spelling_envelope(tmp_path)["typos_local"]

    assert overlay is not None
    assert overlay["parsed"] is None
    assert reason in str(overlay["error"]), overlay


def test_gitignore_lines_are_stripped_and_blank_lines_dropped(
    tmp_path: pathlib.Path,
) -> None:
    """The policy compares whole lines, so surrounding space must not count."""
    _write(tmp_path, ".gitignore", "  .typos-oxendict-base.json  \n\n/target/\n")

    gitignore = build_spelling_envelope(tmp_path)["gitignore"]

    assert gitignore == {
        "path": ".gitignore",
        "lines": [".typos-oxendict-base.json", "/target/"],
        "error": None,
    }


def test_vendored_machinery_is_found_by_pattern(tmp_path: pathlib.Path) -> None:
    """Each default pattern matches its legacy file, and nothing else matches."""
    legacy = (
        "scripts/generate_typos_config.py",
        "scripts/typos_rollout.py",
        "scripts/typos_rollout_check.py",
        "scripts/tests/test_typos_rollout_check.py",
        "scripts/tests/test_generate_typos_config.py",
        "scripts/check_phrase_check.py",
    )
    for relative in legacy:
        _write(tmp_path, relative, "")
    _write(tmp_path, "scripts/release.py", "")
    _write(tmp_path, "docs/typos_rollout.py", "")

    assert build_spelling_envelope(tmp_path)["vendored"] == sorted(legacy)


def test_pruned_directories_are_not_scanned(tmp_path: pathlib.Path) -> None:
    """A virtual environment's copy of a script is not the repository's."""
    _write(tmp_path, ".venv/scripts/typos_rollout.py", "")
    _write(tmp_path, "target/scripts/typos_rollout.py", "")

    assert build_spelling_envelope(tmp_path)["vendored"] == []


def test_the_vendored_patterns_are_a_parameter(tmp_path: pathlib.Path) -> None:
    """A manifest can name machinery the defaults do not."""
    _write(tmp_path, "tools/spell.py", "")

    envelope = build_spelling_envelope(tmp_path, ("tools/*.py",))

    assert envelope["vendored"] == ["tools/spell.py"]
    assert "tools/*.py" not in DEFAULT_VENDORED_PATTERNS


def test_a_linked_overlay_outside_the_checkout_is_refused(
    tmp_path: pathlib.Path,
) -> None:
    """A link could carry another tree's file into the audit."""
    outside = _write(tmp_path, "outside/typos.local.toml", "schema = 1\n")
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "typos.local.toml").symlink_to(outside)

    with pytest.raises(OperationalRuleError, match="outside the checkout"):
        build_spelling_envelope(checkout)
