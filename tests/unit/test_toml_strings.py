"""Specify which lines of a TOML document start inside a multi-line string."""

from __future__ import annotations

import pytest

from concordat.rules.toml_strings import string_interior_lines


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("a = 1\nb = 2\n", set()),
        ('a = """\nx\ny\n"""\nb = 1\n', {1, 2, 3}),
        ("a = '''\nx\n'''\nb = 1\n", {1, 2}),
        ('a = """one line"""\nb = 1\n', set()),
        # A quote or marker in a comment or one-line string opens nothing.
        ('# a """ in a comment\nb = "x # y"\nc = 1\n', set()),
        ("a = 'it\"s'\nb = 1\n", set()),
        ('a = ""\nb = """\nx\n"""\n', {2, 3}),
        # An escaped quote does not close a basic multi-line string...
        ('a = """\nx \\"""\ny\n"""\nb = 1\n', {1, 2, 3}),
        # ...but a backslash does not escape in a literal one.
        ("a = '''\nx \\'''\ny = 1\n", {1}),
        # A backslash at a line end continues the string.
        ('a = """x \\\ny\n"""\nb = 1\n', {1, 2}),
        # An unterminated string runs to the end of the text.
        ('a = """\nx\ny', {1, 2}),
    ],
)
def test_the_lines_that_start_inside_a_multiline_string(
    text: str, expected: set[int]
) -> None:
    """Only a line whose first character is string content is reported."""
    assert string_interior_lines(text) == expected, text
