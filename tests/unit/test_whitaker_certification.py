"""Specify QG-002's certification decision and the runtime digest it compares."""

from __future__ import annotations

import typing as typ

import pytest

from concordat.rules.whitaker_certification import (
    STATUS_CERTIFIED,
    STATUS_UNCERTIFIED,
    STATUS_UNKNOWN,
    CertificationUnavailable,
    Floor,
    NoSuchCommit,
    certify_revision,
    is_excluded,
    runtime_digest_of,
)
from concordat.rules.whitaker_provisioning_envelope import (
    build_whitaker_provisioning_envelope,
    pinned_action_revisions,
    with_install_certifications,
)
from concordat.rules.whitaker_revisions import runtime_digest
from tests.unit.whitaker_revisions_support import (
    ACTION_V1,
    DIRECTORY,
    History,
)

if typ.TYPE_CHECKING:
    import pathlib

    import pygit2

pytest_plugins = ("tests.unit.whitaker_revisions_fixtures",)

FLOOR: typ.Final = "f" * 40
TAG: typ.Final = "certified/install-whitaker/0.2.12"
ACTION: typ.Final = "leynos/shared-actions/.github/actions/install-whitaker"


class FakeSource:
    """A certification source with one floor, answering from small tables."""

    def __init__(
        self,
        *,
        on_branch: set[str],
        after_floor: set[str],
        digests: dict[str, str],
        floors: list[Floor] | None = None,
    ) -> None:
        """Record what is on the branch, after the floor, and each digest."""
        self.on_branch = on_branch
        self.after_floor = after_floor
        self.digests = digests
        self._floors = [Floor(TAG, FLOOR)] if floors is None else floors
        self.asked: list[str] = []

    def floors(self) -> list[Floor]:
        """Return the configured floors."""
        return self._floors

    def is_on_default_branch(self, sha: str) -> bool:
        """Report whether *sha* is on the branch, recording the question."""
        self.asked.append("branch")
        return sha in self.on_branch

    def descends_from(self, floor: str, sha: str) -> bool:
        """Report whether *sha* follows the floor, recording the question."""
        self.asked.append("descends")
        return floor == FLOOR and sha in self.after_floor

    def runtime_digest(self, sha: str) -> str | None:
        """Return the digest recorded for *sha*."""
        self.asked.append("digest")
        return self.digests.get(sha)


def _source(**overrides: typ.Any) -> FakeSource:  # noqa: ANN401  # test overrides.
    """Return a source where `a`, `b` and `c` are the interesting commits."""
    base: dict[str, typ.Any] = {
        "on_branch": {FLOOR, "a" * 40, "b" * 40, "c" * 40},
        "after_floor": {FLOOR, "a" * 40, "b" * 40},
        "digests": {
            FLOOR: "same",
            "a" * 40: "same",
            "b" * 40: "other",
            "c" * 40: "same",
        },
    }
    return FakeSource(**{**base, **overrides})


def test_a_descendant_with_unchanged_content_is_certified() -> None:
    """After the floor, with the floor's digest: certified by that tag."""
    verdict = certify_revision(_source(), "a" * 40)

    assert verdict == {"status": STATUS_CERTIFIED, "floor": TAG, "reason": None}


def test_the_floor_itself_is_certified() -> None:
    """A tag certifies the commit it points at."""
    assert certify_revision(_source(), FLOOR)["status"] == STATUS_CERTIFIED


def test_a_descendant_with_changed_content_is_not_certified() -> None:
    """New runtime content waits for a maintainer to tag it."""
    verdict = certify_revision(_source(), "b" * 40)

    assert verdict["status"] == STATUS_UNCERTIFIED
    assert verdict["reason"]
    assert "runtime content" in verdict["reason"]
    assert TAG in verdict["reason"]


def test_a_commit_before_the_floor_is_not_certified() -> None:
    """On main, same content, but older than every floor: not certified."""
    verdict = certify_revision(_source(), "c" * 40)

    assert verdict["status"] == STATUS_UNCERTIFIED
    assert verdict["reason"]
    assert "older than" in verdict["reason"]


def test_a_commit_off_the_default_branch_is_not_certified_and_costs_one_lookup() -> (
    None
):
    """A fork-only or side-branch commit is refused before anything else is read."""
    source = _source()

    verdict = certify_revision(source, "d" * 40)

    assert verdict["status"] == STATUS_UNCERTIFIED
    assert verdict["reason"]
    assert "default branch" in verdict["reason"]
    assert source.asked == ["branch"]


def test_an_unknown_commit_is_not_certified() -> None:
    """A SHA GitHub does not know is a definite no, not an unknown."""

    class Unknown(FakeSource):
        """A source for which the commit does not exist."""

        def is_on_default_branch(self, sha: str) -> bool:
            """Raise as the API does for an unknown commit.

            Raises
            ------
            NoSuchCommit
                Always.
            """
            raise NoSuchCommit(sha)

    verdict = certify_revision(
        Unknown(on_branch=set(), after_floor=set(), digests={}), "e" * 40
    )

    assert verdict["status"] == STATUS_UNCERTIFIED
    assert verdict["reason"]
    assert "names no commit" in verdict["reason"]


@pytest.mark.parametrize("rate_limited", [True, False])
def test_an_unreadable_answer_is_unknown_never_a_refusal(*, rate_limited: bool) -> None:
    """An API that cannot answer says nothing about the revision."""

    class Failing(FakeSource):
        """A source whose digest lookup fails."""

        def runtime_digest(self, sha: str) -> str | None:
            """Fail as an unreachable API does.

            Raises
            ------
            CertificationUnavailable
                Always.
            """
            message = "trees failed"
            raise CertificationUnavailable(message, rate_limited=rate_limited)

    source = Failing(
        on_branch={"a" * 40}, after_floor={"a" * 40}, digests={}, floors=None
    )

    verdict = certify_revision(source, "a" * 40)

    assert verdict["status"] == STATUS_UNKNOWN
    assert verdict["reason"] == "trees failed"


def test_no_certification_tags_certifies_nothing() -> None:
    """Before the first tag exists no revision is certified."""
    verdict = certify_revision(_source(floors=[]), "a" * 40)

    assert verdict["status"] == STATUS_UNCERTIFIED
    assert verdict["reason"]
    assert "no certification tags" in verdict["reason"]


def test_a_revision_without_the_action_directory_is_not_certified() -> None:
    """A commit that never held the action cannot be the action."""
    source = _source(digests={FLOOR: "same"})

    verdict = certify_revision(source, "a" * 40)

    assert verdict["status"] == STATUS_UNCERTIFIED
    assert verdict["reason"]
    assert "no install-whitaker directory" in verdict["reason"]


def test_any_certified_tag_certifies_not_only_the_newest() -> None:
    """Older approved releases stay certified while their tags exist."""
    older = "1" * 40
    source = FakeSource(
        on_branch={older, "a" * 40},
        after_floor={"a" * 40},
        digests={older: "old", "a" * 40: "old"},
        floors=[Floor("certified/install-whitaker/0.2.11", older), Floor(TAG, FLOOR)],
    )

    class Both(FakeSource):
        """A source where `a` follows the older floor only."""

        def descends_from(self, floor: str, sha: str) -> bool:
            """Report descent from the older floor alone."""
            return floor == older and sha == "a" * 40

    both = Both(
        on_branch=source.on_branch,
        after_floor=source.after_floor,
        digests={**source.digests, FLOOR: "new"},
        floors=source.floors(),
    )

    verdict = certify_revision(both, "a" * 40)

    assert verdict["status"] == STATUS_CERTIFIED
    assert verdict["floor"] == "certified/install-whitaker/0.2.11"


@pytest.mark.parametrize(
    ("path", "excluded"),
    [
        ("tests/test_action.py", True),
        ("tests/fixtures/x.json", True),
        ("README.md", True),
        ("scripts/NOTES.md", True),
        ("action.yml", False),
        ("scripts/install.sh", False),
        ("testsuite/run.sh", False),
        ("scripts/tests/helper.sh", False),
    ],
)
def test_only_the_tests_tree_and_markdown_are_excluded(
    path: str, *, excluded: bool
) -> None:
    """Everything else, including a file added later, counts as runtime."""
    assert is_excluded(path) is excluded


def test_the_digest_skips_excluded_entries_and_ignores_order() -> None:
    """The digest covers runtime files only, whatever order they arrive in."""
    runtime = [("action.yml", "100644", "a" * 40), ("s/x.sh", "100755", "b" * 40)]
    noise = [("tests/t.py", "100644", "c" * 40), ("README.md", "100644", "d" * 40)]

    assert runtime_digest_of([*noise, *reversed(runtime)]) == runtime_digest_of(runtime)


@pytest.mark.parametrize(
    "change",
    [
        ("action.yml", "100644", "9" * 40),
        ("action.yml", "100755", "a" * 40),
        ("renamed.yml", "100644", "a" * 40),
    ],
    ids=["content", "mode", "name"],
)
def test_the_digest_moves_for_content_mode_and_name(
    change: tuple[str, str, str],
) -> None:
    """A change to what the action runs changes the digest."""
    base = [("action.yml", "100644", "a" * 40)]

    assert runtime_digest_of([change]) != runtime_digest_of(base)


def test_an_added_runtime_file_moves_the_digest() -> None:
    """An unknown new file fails closed."""
    base = [("action.yml", "100644", "a" * 40)]
    added = [*base, ("extra.sh", "100644", "b" * 40)]

    assert runtime_digest_of(added) != runtime_digest_of(base)


def test_the_api_form_digest_equals_the_clone_form(history: History) -> None:
    """The digest computed from API tree entries equals the clone's.

    The audit sees the action through the trees API and a maintainer through a
    clone; a floor certified from one must compare equal in the other.
    """
    files = {
        f"{DIRECTORY}/action.yml": ACTION_V1,
        f"{DIRECTORY}/scripts/install.sh": "echo install\n",
        f"{DIRECTORY}/tests/test_action.py": "# test\n",
        f"{DIRECTORY}/README.md": "guide\n",
    }
    sha = history.commit(
        files, "action", executable=frozenset({f"{DIRECTORY}/scripts/install.sh"})
    )
    commit = history.repository[sha]
    tree = history.repository[commit.tree[DIRECTORY].id]
    entries: list[tuple[str, str, str]] = []

    def walk(node: typ.Any, prefix: str = "") -> None:  # noqa: ANN401  # pygit2 tree.
        """Collect every blob below *node* as the trees API lists it."""
        for entry in node:
            if entry.filemode == 0o040000:
                walk(history.repository[entry.id], f"{prefix}{entry.name}/")
            else:
                entries.append((
                    f"{prefix}{entry.name}",
                    f"{entry.filemode:o}",
                    str(entry.id),
                ))

    walk(tree)

    assert runtime_digest_of(entries) == runtime_digest(
        history.repository, typ.cast("pygit2.Commit", commit), DIRECTORY
    )


def _write_pin(root: pathlib.Path, ref: str, name: str = "ci.yml") -> None:
    """Write a workflow that uses install-whitaker at *ref*."""
    path = root / ".github/workflows" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "on: pull_request\njobs:\n  lint:\n    runs-on: ubuntu-latest\n"
        f"    steps:\n      - uses: {ACTION}@{ref}\n",
        encoding="utf-8",
    )


def test_only_full_sha_revisions_of_the_action_are_collected(
    tmp_path: pathlib.Path,
) -> None:
    """A tag, a branch, a short SHA, a fork and another action are not asked about."""
    _write_pin(tmp_path, "a" * 40, "one.yml")
    _write_pin(tmp_path, "a" * 40, "two.yml")
    _write_pin(tmp_path, "b" * 40, "three.yml")
    _write_pin(tmp_path, "main", "four.yml")
    _write_pin(tmp_path, "abc1234", "five.yml")
    (tmp_path / ".github/workflows/six.yml").write_text(
        "on: pull_request\njobs:\n  lint:\n    runs-on: ubuntu-latest\n    steps:\n"
        "      - uses: someone/shared-actions/.github/actions/"
        f"install-whitaker@{'c' * 40}\n"
        f"      - uses: actions/checkout@{'d' * 40}\n",
        encoding="utf-8",
    )

    envelope = build_whitaker_provisioning_envelope(tmp_path)

    assert pinned_action_revisions(envelope, ACTION) == ["a" * 40, "b" * 40]


def test_a_composite_actions_steps_are_collected_too(tmp_path: pathlib.Path) -> None:
    """A composite action that pins install-whitaker is a use like a workflow's."""
    path = tmp_path / ".github/actions/setup/action.yml"
    path.parent.mkdir(parents=True)
    path.write_text(
        f"runs:\n  using: composite\n  steps:\n    - uses: {ACTION}@{'e' * 40}\n",
        encoding="utf-8",
    )

    envelope = build_whitaker_provisioning_envelope(tmp_path)

    assert pinned_action_revisions(envelope, ACTION) == ["e" * 40]


def test_a_checkout_that_pins_nothing_never_calls_the_certifier(
    tmp_path: pathlib.Path,
) -> None:
    """No pin, no lookup: the client is never built."""
    (tmp_path / "Makefile").write_text("lint:\n\twhitaker --all\n", encoding="utf-8")

    def refuse(_sha: str) -> typ.NoReturn:
        """Fail the test if the certifier is called."""
        pytest.fail("the certifier must not be called")

    envelope = with_install_certifications(
        build_whitaker_provisioning_envelope(tmp_path), refuse, ACTION
    )

    assert envelope["install_certifications"] == {}


def test_each_distinct_revision_is_certified_once(tmp_path: pathlib.Path) -> None:
    """A revision repeated across workflows costs one question."""
    _write_pin(tmp_path, "a" * 40, "one.yml")
    _write_pin(tmp_path, "a" * 40, "two.yml")
    asked: list[str] = []

    def certifier(sha: str) -> typ.Any:  # noqa: ANN401  # a stand-in verdict.
        """Record *sha* and certify it."""
        asked.append(sha)
        return {"status": STATUS_CERTIFIED, "floor": TAG, "reason": None}

    envelope = with_install_certifications(
        build_whitaker_provisioning_envelope(tmp_path), certifier, ACTION
    )

    assert asked == ["a" * 40]
    assert set(envelope["install_certifications"]) == {"a" * 40}
