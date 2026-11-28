"""Unit tests for the `dylint.toml` line scan that places each exclusion.

The scan finds each `excluded_*` key, the table it sits in and whether a comment
block states its reason, without being fooled by multi-line strings.
"""

from __future__ import annotations

import pytest

from concordat.rules.whitaker_exclusions_text import locate_exclusions


class TestReasons:
    """Where a reason may sit, and what is not one."""

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ('[t]\n# why\nexcluded_paths = ["a"]\n', True),
            ('# why\n[t]\nexcluded_paths = ["a"]\n', True),
            ('[t]\nexcluded_paths = ["a"]\n', False),
            ('[t]\n# why\n\nexcluded_paths = ["a"]\n', False),
            ('[t]\n#\n#   \nexcluded_paths = ["a"]\n', False),
            ('# why\n\n[t]\nexcluded_paths = ["a"]\n', False),
            ('# why\nexcluded_paths = ["a"]\n', True),
            ('[t]\n  # indented why\n  excluded_paths = ["a"]\n', True),
        ],
    )
    def test_a_reason_is_a_non_empty_comment_block_directly_above(
        self, text: str, *, expected: bool
    ) -> None:
        """A blank line ends a block; a bare marker says nothing."""
        [found] = locate_exclusions(text)
        assert found.has_reason is expected

    def test_the_table_and_line_of_each_key_are_recorded(self) -> None:
        """The finding names the key's line in the file."""
        text = '[a]\nexcluded_paths = ["x"]\n\n[b.c]\nexcluded_crates = ["y"]\n'
        found = locate_exclusions(text)
        assert [(f.table, f.key, f.line) for f in found] == [
            ("a", "excluded_paths", 2),
            ("b.c", "excluded_crates", 5),
        ]

    def test_a_key_that_is_not_at_the_start_of_a_line_is_not_found(self) -> None:
        """A dotted key is left for the policy to report as not located."""
        assert locate_exclusions('a.excluded_crates = ["x"]\n') == []


class TestMultilineStrings:
    """A line inside a multi-line string is content, not structure."""

    def test_a_quoted_key_and_comment_do_not_become_a_real_keys_reason(self) -> None:
        """CodeRabbit's example: the string holds both a comment and a key."""
        text = (
            '[t]\nexcluded_paths = ["loader"]\nnote = """\n'
            '# not a reason\nexcluded_paths = ["bogus"]\n"""\n'
        )
        found = locate_exclusions(text)
        assert [(f.key, f.line, f.has_reason) for f in found] == [
            ("excluded_paths", 2, False)
        ]

    @pytest.mark.parametrize(
        "text",
        [
            "[t]\nnote = '''\n[fake]\nexcluded_paths = [\"x\"]\n'''\n",
            '[t]\nnote = """\n[fake]\nexcluded_paths = ["x"]\n"""\n',
            '[t]\nnote = """a \\\nexcluded_paths = ["x"]\nb"""\n',
        ],
    )
    def test_a_header_or_key_inside_a_string_is_not_found(self, text: str) -> None:
        """Whichever quote opens the string, and across an escaped line break."""
        assert locate_exclusions(text) == []

    def test_a_key_after_the_string_ends_is_found(self) -> None:
        """The scan leaves the string at its closing quotes."""
        text = '[t]\nnote = """\nx\n"""\n# why\nexcluded_paths = ["a"]\n'
        [found] = locate_exclusions(text)
        assert (found.line, found.has_reason) == (6, True)

    def test_a_comment_line_inside_a_string_is_not_a_reason(self) -> None:
        """The block above the key stops at string content."""
        text = '[t]\nnote = """\n# inside\n"""\nexcluded_paths = ["a"]\n'
        [found] = locate_exclusions(text)
        assert found.has_reason is False

    def test_quotes_and_hashes_in_single_line_strings_are_not_structure(self) -> None:
        """A `#` in a string is not a comment, and a lone quote ends at the line."""
        text = 'a = "x # y"\nb = \'it\'\n[t]\n# why\nexcluded_paths = ["a"]\n'
        [found] = locate_exclusions(text)
        assert (found.table, found.line, found.has_reason) == ("t", 5, True)
