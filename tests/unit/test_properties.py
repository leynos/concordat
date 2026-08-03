"""Property tests for the validation and selection rules.

Each of these encodes a rule that example-based tests can only sample: an
identifier grammar, a filtering contract, or a selection rule over a
generated history. Where a property restates a regex, it is written from the
specification rather than the implementation's pattern, so the two can
disagree.
"""

from __future__ import annotations

import pathlib
import string

import pytest
from hypothesis import given
from hypothesis import strategies as st

from concordat import credentials, xdg
from concordat.credentials import CREDENTIAL_KEYS
from concordat.errors import ConcordatError
from concordat.rules import runner
from scripts import parabellum_sweep as sweep

_ALNUM = string.ascii_letters + string.digits


def _is_valid_owner(owner: str) -> bool:
    """Return whether *owner* satisfies the documented owner grammar.

    Written from the rule — alphanumeric ends, alphanumerics and hyphens
    within — rather than from `xdg._OWNER_PATTERN`, so a change to the
    pattern is a change this can detect.
    """
    if not owner:
        return False
    if owner[0] not in _ALNUM or owner[-1] not in _ALNUM:
        return False
    return all(character in _ALNUM + "-" for character in owner)


class TestOwnerNames:
    """`xdg.validate_owner` accepts exactly the documented owner grammar."""

    @given(st.text(alphabet=_ALNUM + "-_./ ", max_size=12))
    def test_matches_the_documented_grammar(self, owner: str) -> None:
        """Acceptance agrees with the grammar for every generated string."""
        try:
            xdg.validate_owner(owner)
        except ConcordatError:
            accepted = False
        else:
            accepted = True
        assert accepted == _is_valid_owner(owner), (
            f"{owner!r}: validate_owner said {accepted}, "
            f"grammar says {_is_valid_owner(owner)}"
        )

    @given(
        st.text(alphabet=_ALNUM, min_size=1, max_size=1),
        st.text(alphabet=_ALNUM + "-", max_size=10),
        st.text(alphabet=_ALNUM, min_size=1, max_size=1),
    )
    def test_well_formed_owners_round_trip(
        self,
        head: str,
        middle: str,
        tail: str,
    ) -> None:
        """A name built to the grammar is returned unchanged."""
        owner = head + middle + tail
        assert xdg.validate_owner(owner) == owner, owner

    @given(st.text(alphabet=_ALNUM + "-", max_size=10))
    def test_a_separator_is_never_accepted(self, stem: str) -> None:
        """No owner containing a path separator is ever accepted."""
        for candidate in (f"{stem}/x", f"x/{stem}", f"..{stem}", f"{stem}\\x"):
            with pytest.raises(ConcordatError):
                xdg.validate_owner(candidate)


class TestCredentialFiltering:
    """Only recognized, non-blank string values become credentials."""

    @given(
        st.dictionaries(
            keys=st.one_of(
                st.sampled_from(CREDENTIAL_KEYS),
                st.text(max_size=8),
                st.integers(),
            ),
            values=st.one_of(
                st.text(max_size=8),
                st.integers(),
                st.booleans(),
                st.none(),
                st.lists(st.text(max_size=3), max_size=2),
            ),
            max_size=6,
        )
    )
    def test_only_recognized_non_blank_strings_survive(
        self,
        loaded: dict[object, object],
    ) -> None:
        """Every surviving entry is a recognized key with a trimmed string."""
        result = credentials._recognised_credentials(loaded)

        for key, value in result.items():
            assert key in CREDENTIAL_KEYS, f"unrecognized key survived: {key!r}"
            assert isinstance(value, str), f"{key}: {value!r} is not a string"
            assert value == value.strip(), f"{key}: {value!r} was not trimmed"
            assert value, f"{key}: a blank value survived"
        for key, value in loaded.items():
            expected = (
                isinstance(key, str)
                and key in CREDENTIAL_KEYS
                and isinstance(value, str)
                and bool(value.strip())
            )
            assert (key in result) == expected, (
                f"{key!r}={value!r}: survived={key in result}, expected={expected}"
            )


class TestRulePackageIdentifiers:
    """Rule package identifiers admit only the canonical hyphenated form."""

    @given(
        st.text(alphabet=string.ascii_lowercase + string.digits + "-./_", max_size=14)
    )
    def test_acceptance_agrees_with_the_grammar(self, rule_id: str) -> None:
        """Acceptance matches "lower-case words joined by single hyphens"."""
        words = rule_id.split("-")
        expected = bool(rule_id) and all(
            word and all(c in string.ascii_lowercase + string.digits for c in word)
            for word in words
        )
        try:
            runner._validated_rule_id(rule_id)
        except Exception:  # noqa: BLE001 - any rejection counts as "not accepted"
            accepted = False
        else:
            accepted = True
        assert accepted == expected, (
            f"{rule_id!r}: accepted={accepted}, want={expected}"
        )

    @given(st.text(max_size=12))
    def test_a_rejected_identifier_never_reaches_the_filesystem(
        self,
        rule_id: str,
    ) -> None:
        """A rejected identifier raises before any path is resolved."""
        try:
            runner._validated_rule_id(rule_id)
        except Exception:  # noqa: BLE001 - rejection is the property under test
            return
        assert "/" not in rule_id, rule_id
        assert "\\" not in rule_id, rule_id
        assert rule_id not in {".", ".."}, rule_id


class TestManifestRepositoryNames:
    """Manifest repository names stay a single, safe path component."""

    @given(st.text(alphabet=_ALNUM + "._-/\\ ", max_size=12))
    def test_accepted_names_are_safe_components(self, name: str) -> None:
        """Any accepted name is one component and is not a dot segment."""
        try:
            sweep._validated_identifier(
                name, sweep._REPO_NAME_PATTERN, "repository name", "m"
            )
        except sweep.OperationalRuleError:
            return
        assert "/" not in name, name
        assert "\\" not in name, name
        assert name not in {".", ".."}, name
        assert name.strip("."), f"{name!r} is a dot-only segment"

    @given(st.text(alphabet=_ALNUM + "._-", min_size=1, max_size=12))
    def test_a_component_joined_to_a_root_stays_inside_it(self, name: str) -> None:
        """An accepted name cannot escape the directory it is joined to.

        A real directory is used rather than `tmp_path` because Hypothesis
        rejects function-scoped fixtures: they would be created once and
        shared across every generated example.
        """
        try:
            sweep._validated_identifier(
                name, sweep._REPO_NAME_PATTERN, "repository name", "m"
            )
        except sweep.OperationalRuleError:
            return
        root = pathlib.Path(__file__).resolve().parent
        assert (root / name).resolve().is_relative_to(root), name


_REPOS = ("leynos/alpha", "leynos/beta", "leynos/gamma")


def _record(repository: str, verdict: str, sha: str | None) -> dict[str, object]:
    """Build a minimal ledger record."""
    return {"repository": repository, "verdict": verdict, "commit_sha": sha}


_LEDGER = st.lists(
    st.builds(
        _record,
        st.sampled_from(_REPOS),
        st.sampled_from(("compliant", "noncompliant", "indeterminate", "excluded")),
        st.one_of(st.none(), st.sampled_from(("a" * 40, "b" * 40))),
    ),
    max_size=8,
)


class TestLedgerSelection:
    """Latest-record selection and the idempotency lookup over a history."""

    @given(_LEDGER)
    def test_latest_record_is_the_last_appended(
        self,
        ledger: list[dict[str, object]],
    ) -> None:
        """The report shows each repository's final record, not its first.

        The ledger is append-only, so a re-audit adds a row rather than
        replacing one; selection must therefore be last-wins.
        """
        latest = sweep._latest_records(ledger)

        assert set(latest) == {r["repository"] for r in ledger}, latest
        for repository, record in latest.items():
            tail = [r for r in ledger if r["repository"] == repository][-1]
            assert record is tail, f"{repository}: expected the last record"

    @given(_LEDGER)
    def test_idempotency_lookup_matches_a_scan(
        self,
        ledger: list[dict[str, object]],
    ) -> None:
        """`_already_ledgered` agrees with a direct scan of the history.

        A commit SHA asks "has this exact commit been recorded"; `None` asks
        the different question "is this repository excluded".
        """
        for repository in _REPOS:
            excluded = any(
                r["repository"] == repository and r["verdict"] == "excluded"
                for r in ledger
            )
            assert (
                sweep._already_ledgered(ledger, repository, commit_sha=None) == excluded
            ), repository
            for sha in ("a" * 40, "b" * 40):
                seen = any(
                    r["repository"] == repository and r["commit_sha"] == sha
                    for r in ledger
                )
                assert (
                    sweep._already_ledgered(ledger, repository, commit_sha=sha) == seen
                ), f"{repository}@{sha[:6]}"
