"""Specify which shell shapes RT-028 reads from a workflow `run` script.

Each case is a script whose text says something about the selection it makes:
a date an `echo` prints is data, a here-document body is input, and a
substitution, a group, an assignment or a wrapper's program is a selection.
The table is a truth table over the shell constructs, so each row states one
normative case rather than repeating a test body.
"""

from __future__ import annotations

import typing as typ

import pytest

from tests.unit.toolchain_literal_support import run_step, rust_repository, texts, write

if typ.TYPE_CHECKING:
    import pathlib

_CASES: typ.Final = [
    pytest.param(
        'echo "cargo +nightly-2026-10-30 test"\n'
        "true; cargo +nightly-2026-11-05 check\n",
        ["nightly-2026-11-05"],
        id="quoted-text-is-an-argument",
    ),
    pytest.param(
        "cat <<'EOF' > notes.txt\n"
        "cargo +nightly-2026-10-30 check\n"
        "EOF\n"
        "cargo +nightly-2026-09-01 test\n",
        ["nightly-2026-09-01"],
        id="quoted-here-document-body-is-data",
    ),
    pytest.param(
        "export RUSTUP_TOOLCHAIN=nightly-2026-10-30\ncargo check\n",
        ["nightly-2026-10-30"],
        id="export-is-a-selection",
    ),
    pytest.param(
        "RUSTUP_TOOLCHAIN=nightly-2026-10-30\nexport RUSTUP_TOOLCHAIN\ncargo test\n",
        ["nightly-2026-10-30"],
        id="assignment-alone-selects-for-what-follows",
    ),
    pytest.param(
        "env -u RUSTUP_TOOLCHAIN cargo +nightly-2026-10-30 test\n",
        ["nightly-2026-10-30"],
        id="wrapper-operand-is-not-the-program",
    ),
    pytest.param(
        "if RUSTUP_TOOLCHAIN=nightly-2026-10-30 cargo test; then true; fi\n",
        ["nightly-2026-10-30"],
        id="assignment-after-a-keyword",
    ),
    pytest.param(
        "cat <<EOF; cargo +nightly-2026-10-30 test\n"
        "cargo +nightly-2026-09-01 check\n"
        "EOF\n",
        ["nightly-2026-10-30"],
        id="command-before-the-here-document-body",
    ),
    pytest.param(
        'cat <<< "hello"\ncargo +nightly-2026-10-30 test\n',
        ["nightly-2026-10-30"],
        id="here-string-hides-nothing",
    ),
    pytest.param(
        'echo "\\"; cargo +nightly-2026-10-30 test; \\""\n',
        [],
        id="escaped-quote-keeps-the-argument",
    ),
    pytest.param(
        'version="$(cargo +nightly-2026-10-30 --version)"\n',
        ["nightly-2026-10-30"],
        id="command-substitution-runs",
    ),
    pytest.param(
        "sudo -n cargo +nightly-2026-10-30 test\n"
        "xargs -n 1 cargo +nightly-2026-09-01 test\n",
        ["nightly-2026-10-30", "nightly-2026-09-01"],
        id="option-arity-follows-the-wrapper",
    ),
    pytest.param(
        "cat<<'EOF'\ncargo +nightly-2026-09-01 check\nEOF\n"
        "cargo +nightly-2026-10-30 test\n",
        ["nightly-2026-10-30"],
        id="unspaced-here-document",
    ),
    pytest.param(
        "(cargo +nightly-2026-10-30 check)\n",
        ["nightly-2026-10-30"],
        id="parenthesized-group-runs",
    ),
    pytest.param(
        "version=`cargo +nightly-2026-10-30 --version`\n",
        ["nightly-2026-10-30"],
        id="backquoted-substitution-runs",
    ),
    pytest.param(
        "car\\\ngo +nightly-2026-10-30 test\n",
        ["nightly-2026-10-30"],
        id="line-continuation-joins-the-word",
    ),
    pytest.param(
        "cat <<EOF\n$(cargo +nightly-2026-10-30 --version)\nEOF\n",
        ["nightly-2026-10-30"],
        id="unquoted-here-document-body-expands",
    ),
    pytest.param(
        "cat <<'EOF'\n$(cargo +nightly-2026-10-30 --version)\nEOF\n",
        [],
        id="quoted-here-document-body-does-not-expand",
    ),
]


@pytest.mark.parametrize(("script", "expected"), _CASES)
def test_a_run_script_selects_only_where_a_command_names_a_toolchain(
    tmp_path: pathlib.Path, script: str, expected: list[str]
) -> None:
    """Each script selects exactly the toolchains its commands name."""
    rust_repository(tmp_path)
    write(tmp_path, ".github/workflows/ci.yml", run_step(script))

    assert texts(tmp_path) == expected
