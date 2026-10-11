"""Specify how a `netsuke build` command is followed into the Netsukefile."""

from __future__ import annotations

import pathlib
import textwrap
import typing as typ

import pytest
from hypothesis import given
from hypothesis import strategies as st

from concordat.errors import OperationalRuleError
from concordat.rules.documentation_envelope import build_documentation_envelope
from concordat.rules.markdown_envelope import resolved_root
from concordat.rules.netsuke_manifest import (
    ManifestReader,
    Verdict,
    _request,
    _runs_clippy,
    netsuke_verdict,
)

HEADER: typ.Final = 'netsuke_version: "1.0.0"\n\n'


def _verdict(reader: ManifestReader, words: list[str]) -> Verdict:
    """Return the verdict for a `netsuke build` command, which must be one."""
    verdict = netsuke_verdict(reader, words)
    assert verdict is not None
    return verdict


def _reader(root: pathlib.Path, manifest: str | None) -> ManifestReader:
    """Lay out *manifest* as the root Netsukefile and return a reader for it."""
    if manifest is not None:
        (root / "Netsukefile").write_text(textwrap.dedent(manifest), encoding="utf-8")
    return ManifestReader(root, resolved_root(root))


@pytest.mark.parametrize(
    ("words", "manifest", "targets"),
    [
        (["netsuke", "build", "lint"], "Netsukefile", ["lint"]),
        (["netsuke", "build"], "Netsukefile", []),
        (
            ["netsuke", "-f", "ci.yml", "build", "lint", "test"],
            "ci.yml",
            ["lint", "test"],
        ),
        (["netsuke", "-C", "sub", "build", "lint"], "sub/Netsukefile", ["lint"]),
        (["netsuke", "--jobs", "4", "build", "lint"], "Netsukefile", ["lint"]),
        (["PATH=x", "netsuke", "build", "lint"], "Netsukefile", ["lint"]),
        ([" netsuke ", "build", "lint"], "Netsukefile", ["lint"]),
    ],
)
def test_a_netsuke_build_names_its_manifest_and_targets(
    words: list[str], manifest: str, targets: list[str]
) -> None:
    """Options with values are skipped; the words after `build` are targets."""
    request = _request(words)

    assert request is not None
    assert (request.manifest, request.targets) == (manifest, targets)


@pytest.mark.parametrize(
    "words",
    [
        ["cargo", "build", "lint"],
        ["netsuke", "graph"],
        ["echo", "netsuke", "build"],
        [],
    ],
)
def test_other_commands_are_not_netsuke_builds(words: list[str]) -> None:
    """Only `netsuke ... build` is followed."""
    assert _request(words) is None


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("cargo clippy --all-targets", True),
        ("sh -e -c '\"$${CARGO:-cargo}\" clippy {{ flags }} -- -D warnings'", True),
        ('sh -c \'PATH="{{ p }}:$$PATH"; "$${CARGO:-cargo}" doc; cargo clippy\'', True),
        ('bash -c "cargo +nightly clippy"', True),
        ("echo cargo clippy", False),
        ("cargo fmt --check", False),
        ("sh -c 'rustup component add clippy'", False),
        ("true", False),
    ],
)
def test_a_manifest_command_runs_clippy_only_as_a_cargo_command(
    command: str, *, expected: bool
) -> None:
    """The lexer reads the command, descending into `sh -c` scripts."""
    assert _runs_clippy(command) == (expected, None)


def test_an_unreadable_command_that_mentions_clippy_is_a_problem() -> None:
    """A command the lexer rejects cannot be read as absence."""
    found, problem = _runs_clippy("cargo clippy 'unterminated")

    assert (found, problem is not None) == (False, True)


def test_a_clippy_action_is_found_through_a_dependency(tmp_path: pathlib.Path) -> None:
    """`lint` depends on the action that runs clippy."""
    reader = _reader(
        tmp_path,
        HEADER
        + """\
actions:
  - name: lint
    deps: clippy-lint
    command: "true"
  - name: clippy-lint
    command: "cargo clippy"
""",
    )

    assert _verdict(reader, ["netsuke", "build", "lint"]) == Verdict(
        is_run=True, problem=None
    )


def test_a_variant_selected_by_when_counts(tmp_path: pathlib.Path) -> None:
    """Every entry with the name is read, so one variant with clippy is enough."""
    reader = _reader(
        tmp_path,
        HEADER
        + """\
actions:
  - name: lint
    when: a
    command: "cargo fmt"
  - name: lint
    when: not a
    command: "cargo clippy"
""",
    )

    assert _verdict(reader, ["netsuke", "build", "lint"]).is_run is True


def test_defaults_name_the_targets_when_none_are_given(tmp_path: pathlib.Path) -> None:
    """`netsuke build` without targets builds the manifest's defaults."""
    reader = _reader(
        tmp_path,
        HEADER
        + 'actions:\n  - name: lint\n    command: "cargo clippy"\n'
        + "\ndefaults:\n  - lint\n",
    )

    assert _verdict(reader, ["netsuke", "build"]).is_run is True


@pytest.mark.parametrize(
    ("manifest", "fragment"),
    [
        (None, "does not exist"),
        ("actions: [\n", "cannot be parsed"),
        (
            HEADER + 'actions:\n  - name: check\n    command: "cargo clippy"\n',
            "defines no target 'lint'",
        ),
        (HEADER + 'actions:\n  - name: lint\n    command: "true"\n', None),
    ],
)
def test_a_manifest_that_cannot_answer_carries_the_reason(
    tmp_path: pathlib.Path, manifest: str | None, fragment: str | None
) -> None:
    """Fail closed: a missing, unparsable or incomplete manifest is a problem."""
    verdict = _verdict(_reader(tmp_path, manifest), ["netsuke", "build", "lint"])

    assert verdict.is_run is False
    if fragment is None:
        assert verdict.problem is None
    else:
        assert verdict.problem is not None
        assert fragment in verdict.problem


def test_a_dependency_cycle_terminates(tmp_path: pathlib.Path) -> None:
    """Targets that depend on each other do not loop."""
    reader = _reader(
        tmp_path,
        HEADER
        + """\
actions:
  - name: lint
    deps: other
    command: "true"
  - name: other
    deps: lint
    command: "true"
""",
    )

    assert _verdict(reader, ["netsuke", "build", "lint"]) == Verdict(
        is_run=False, problem=None
    )


def test_a_manifest_outside_the_checkout_is_refused(tmp_path: pathlib.Path) -> None:
    """`-C ..` cannot point the audit at a manifest outside the checkout."""
    reader = _reader(tmp_path, None)

    verdict = _verdict(reader, ["netsuke", "-C", "..", "build", "lint"])

    assert verdict is not None
    assert verdict.problem is not None
    assert "outside the checkout" in verdict.problem


def test_a_symlinked_manifest_outside_the_checkout_is_refused(
    tmp_path: pathlib.Path,
) -> None:
    """The containment guard applies to the manifest as to every other input."""
    outside = tmp_path / "outside.yml"
    outside.write_text(HEADER + "actions: []\n", encoding="utf-8")
    checkout = tmp_path / "repo"
    checkout.mkdir()
    (checkout / "Netsukefile").symlink_to(outside)

    with pytest.raises(OperationalRuleError, match="outside the checkout"):
        netsuke_verdict(
            ManifestReader(checkout, resolved_root(checkout)),
            ["netsuke", "build", "lint"],
        )


def test_the_envelope_records_a_clippy_run_found_through_netsuke(
    tmp_path: pathlib.Path,
) -> None:
    """End to end through the Makefile expansion."""
    (tmp_path / "Makefile").write_text(
        ".PHONY: lint\nNETSUKE ?= netsuke\nlint:\n\t$(NETSUKE) build lint\n",
        encoding="utf-8",
    )
    (tmp_path / "Netsukefile").write_text(
        HEADER + 'actions:\n  - name: lint\n    command: "cargo clippy"\n',
        encoding="utf-8",
    )

    runs = build_documentation_envelope(tmp_path)["clippy_runs"]

    assert [run["resolved"] for run in runs] == [True]


@given(st.integers(min_value=1, max_value=6), st.integers(min_value=0, max_value=6))
def test_clippy_is_found_at_any_depth_in_a_dependency_chain(
    depth: int, clippy_at: int
) -> None:
    """A chain of `deps` reaches clippy exactly when the chain contains it."""
    import tempfile

    names = [f"step{i}" for i in range(depth)]
    lines = []
    for index, name in enumerate(names):
        deps = f"    deps: {names[index + 1]}\n" if index + 1 < depth else ""
        command = "cargo clippy" if index == clippy_at else "true"
        lines.append(f'  - name: {name}\n{deps}    command: "{command}"\n')
    manifest = HEADER + "actions:\n" + "".join(lines)
    with tempfile.TemporaryDirectory() as tmp:
        reader = _reader(pathlib.Path(tmp), manifest)
        verdict = _verdict(reader, ["netsuke", "build", "step0"])

    assert verdict.is_run is (clippy_at < depth)
