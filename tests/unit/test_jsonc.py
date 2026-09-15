"""Unit tests for the JSONC decoder behind the markdownlint configuration fact.

markdownlint-cli2 accepts comments and trailing commas in
`.markdownlint-cli2.jsonc`; `concordat.rules.jsonc` must accept exactly the
same relaxations and no others, and must never treat comment syntax inside a
string literal as a comment.
"""

from __future__ import annotations

import json

import pytest
from hypothesis import given
from hypothesis import strategies as st

from concordat.rules.jsonc import JsoncError, loads_jsonc


class TestLoadsJsonc:
    """The decoder accepts JSONC and rejects everything else."""

    def test_plain_json_decodes_unchanged(self) -> None:
        """A strict JSON document is decoded exactly as `json.loads` would."""
        document = '{"config": {"MD013": {"line_length": 80}}, "ignores": ["a"]}'
        assert loads_jsonc(document) == json.loads(document)

    def test_line_and_block_comments_are_stripped(self) -> None:
        """Both comment forms are removed outside string literals."""
        document = """{
          // leading comment
          "config": { /* inline */ "MD004": { "style": "dash" } }, // trailing
          /* multi
             line */
          "ignores": []
        }"""
        assert loads_jsonc(document) == {
            "config": {"MD004": {"style": "dash"}},
            "ignores": [],
        }

    def test_trailing_commas_are_stripped(self) -> None:
        """A comma before `}` or `]` is tolerated, including across newlines."""
        document = '{"ignores": ["a", "b",], "config": {"MD010": false,},\n}'
        assert loads_jsonc(document) == {
            "ignores": ["a", "b"],
            "config": {"MD010": False},
        }

    @pytest.mark.parametrize(
        ("document", "expected"),
        [
            pytest.param('{"url": "http://x/y"}', {"url": "http://x/y"}, id="slashes"),
            pytest.param('{"note": "/* kept */"}', {"note": "/* kept */"}, id="block"),
            pytest.param('{"glob": "a,}"}', {"glob": "a,}"}, id="comma-brace"),
            pytest.param('{"q": "say \\"//\\""}', {"q": 'say "//"'}, id="escaped"),
        ],
    )
    def test_comment_syntax_inside_strings_is_preserved(
        self, document: str, expected: dict[str, str]
    ) -> None:
        """Comment markers and commas inside a string literal are content."""
        assert loads_jsonc(document) == expected

    def test_unterminated_block_comment_is_rejected(self) -> None:
        """A block comment that never closes is a decoding error."""
        with pytest.raises(JsoncError, match="unterminated block comment"):
            loads_jsonc('{"a": 1} /* open')

    def test_invalid_json_reports_its_position(self) -> None:
        """An invalid document names the line and column of the failure."""
        with pytest.raises(JsoncError, match=r"line 2 column \d+"):
            loads_jsonc('{\n  "a": tru\n}')

    def test_single_quoted_strings_are_still_rejected(self) -> None:
        """Only comments and trailing commas are relaxed; JSON5 is not."""
        with pytest.raises(JsoncError):
            loads_jsonc("{'a': 1}")


_json_scalars = st.none() | st.booleans() | st.integers() | st.text()
_json_values = st.recursive(
    _json_scalars,
    lambda children: st.lists(children) | st.dictionaries(st.text(), children),
    max_leaves=20,
)


@given(_json_values)
def test_strict_json_round_trips_through_the_decoder(value: object) -> None:
    """Any value `json.dumps` emits is decoded back to itself."""
    assert loads_jsonc(json.dumps(value)) == value


@given(_json_values)
def test_comments_around_a_document_do_not_change_its_value(value: object) -> None:
    """Wrapping comments and a trailing comma leave the decoded value intact."""
    document = json.dumps(value, indent=1)
    wrapped = f"// head\n/* block */ {document} // tail"
    assert loads_jsonc(wrapped) == value
