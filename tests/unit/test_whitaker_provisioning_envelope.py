"""Specify the Whitaker provisioning envelope and its rule package end to end."""

from __future__ import annotations

import os
import typing as typ

import pygit2
import pytest

from concordat.errors import OperationalRuleError
from concordat.rules import packages, runner
from concordat.rules.whitaker_provisioning_envelope import (
    ENVELOPE_KIND,
    MAX_SCRIPT_BYTES,
    build_whitaker_provisioning_envelope,
)

if typ.TYPE_CHECKING:
    import pathlib

RULE_ID: typ.Final = "whitaker-provisioning"

BINSTALL_WORKFLOW: typ.Final = """\
on: pull_request
jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - run: cargo binstall --no-confirm whitaker-installer@0.2.9
"""


def _write(root: pathlib.Path, relative: str, text: str) -> None:
    """Write *text* to *relative* under *root*, creating its directories."""
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _paths(envelope: typ.Mapping[str, object], surface: str) -> list[str]:
    """Return the recorded paths of one surface of *envelope*."""
    facts = typ.cast("list[dict[str, object]]", envelope[surface])
    return [typ.cast("str", fact["path"]) for fact in facts]


class TestSurfaces:
    """Cover which files reach the policy, and how."""

    def test_records_every_provisioning_surface(self, tmp_path: pathlib.Path) -> None:
        """Workflows and actions are decoded; Makefiles and scripts are text."""
        _write(tmp_path, ".github/workflows/ci.yml", BINSTALL_WORKFLOW)
        _write(
            tmp_path, ".github/actions/setup/action.yml", "runs: {using: composite}\n"
        )
        _write(tmp_path, "Makefile", "lint:\n\twhitaker --all\n")
        _write(tmp_path, "mk/tools.mk", "tools:\n")
        _write(tmp_path, "scripts/install.sh", "echo hi\n")
        _write(tmp_path, ".github/scripts/check.py", "print('hi')\n")
        _write(tmp_path, "bootstrap", "#!/bin/sh\necho hi\n")

        envelope = build_whitaker_provisioning_envelope(tmp_path)

        assert envelope["kind"] == ENVELOPE_KIND
        assert _paths(envelope, "workflows") == [".github/workflows/ci.yml"]
        assert _paths(envelope, "actions") == [".github/actions/setup/action.yml"]
        assert sorted(_paths(envelope, "scripts")) == [
            ".github/scripts/check.py",
            "Makefile",
            "bootstrap",
            "mk/tools.mk",
            "scripts/install.sh",
        ]

    def test_leaves_out_what_ci_does_not_run(self, tmp_path: pathlib.Path) -> None:
        """Dependencies, build output, prose and root files without a shebang.

        A vendored script under `node_modules` or `target` is not this
        repository's automation, and a root README is not a script.
        """
        _write(tmp_path, "node_modules/pkg/scripts/install.sh", "curl x\n")
        _write(tmp_path, "target/debug/build.sh", "curl x\n")
        _write(tmp_path, "README.md", "cargo install whitaker-installer\n")
        _write(tmp_path, "notes", "no shebang\n")
        _write(tmp_path, "src/helper.sh", "echo hi\n")

        envelope = build_whitaker_provisioning_envelope(tmp_path)

        assert _paths(envelope, "scripts") == []

    def test_leaves_out_a_binary(self, tmp_path: pathlib.Path) -> None:
        """A compiled tool under `bin/` is not text any shell reads."""
        binary = tmp_path / "bin" / "tool"
        binary.parent.mkdir()
        binary.write_bytes(b"\x7fELF\xff\xfe\x00")

        envelope = build_whitaker_provisioning_envelope(tmp_path)

        assert _paths(envelope, "scripts") == []

    def test_records_an_oversized_script_as_unreadable(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A script too large to load is evidence the policy cannot read."""
        _write(tmp_path, "scripts/huge.sh", "#" * (MAX_SCRIPT_BYTES + 1))

        envelope = build_whitaker_provisioning_envelope(tmp_path)

        (fact,) = typ.cast("list[dict[str, object]]", envelope["scripts"])
        assert fact["text"] is None
        assert "larger than" in typ.cast("str", fact["error"])

    def test_reads_a_symlink_within_the_checkout_as_its_target(
        self, tmp_path: pathlib.Path
    ) -> None:
        """`bin/tool-x -> tool` is the repository's own script, twice named."""
        _write(tmp_path, "bin/tool", "cargo install whitaker-installer\n")
        (tmp_path / "bin" / "tool-x").symlink_to("tool")

        envelope = build_whitaker_provisioning_envelope(tmp_path)

        facts = typ.cast("list[dict[str, object]]", envelope["scripts"])
        assert [fact["text"] for fact in facts] == [
            "cargo install whitaker-installer\n",
            "cargo install whitaker-installer\n",
        ]

    def test_records_a_symlink_leaving_the_checkout_as_unreadable(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A link could point anywhere, so a target outside is not read."""
        outside = tmp_path / "outside.sh"
        outside.write_text("cargo install whitaker-installer\n", encoding="utf-8")
        checkout = tmp_path / "checkout"
        (checkout / "scripts").mkdir(parents=True)
        (checkout / "scripts" / "install.sh").symlink_to(outside)

        envelope = build_whitaker_provisioning_envelope(checkout)

        (fact,) = typ.cast("list[dict[str, object]]", envelope["scripts"])
        assert fact == {
            "path": "scripts/install.sh",
            "text": None,
            "error": "symlink that leaves the checkout",
        }

    def test_records_a_dangling_symlink_as_unreadable(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A link that resolves nowhere is evidence of a gap, not an absence."""
        (tmp_path / "scripts").mkdir()
        (tmp_path / "scripts" / "install.sh").symlink_to("missing.sh")

        envelope = build_whitaker_provisioning_envelope(tmp_path)

        (fact,) = typ.cast("list[dict[str, object]]", envelope["scripts"])
        assert fact["text"] is None
        assert fact["error"] == "symlink that leaves the checkout"

    @pytest.mark.skipif(
        os.name == "nt" or os.geteuid() == 0,
        reason="a file mode only denies reading to an unprivileged POSIX user",
    )
    @pytest.mark.parametrize(
        "relative", ["scripts/install.sh", "scripts/setup", "installer"]
    )
    def test_records_an_unreadable_script_with_its_reason(
        self, tmp_path: pathlib.Path, relative: str
    ) -> None:
        """A script that cannot be read is kept, so the audit is indeterminate.

        The root-level and suffixless cases matter because a shebang decides
        whether they are scripts at all; a failed read must not answer no.
        """
        _write(tmp_path, relative, "#!/bin/sh\ncurl whitaker-installer\n")
        script = tmp_path / relative
        script.chmod(0)
        try:
            envelope = build_whitaker_provisioning_envelope(tmp_path)
            result = runner.run_rule(RULE_ID, tmp_path)
        finally:
            script.chmod(0o600)

        (fact,) = typ.cast("list[dict[str, object]]", envelope["scripts"])
        assert fact["text"] is None
        assert "cannot read" in typ.cast("str", fact["error"])
        assert result.verdict == "indeterminate", result

    def test_records_a_malformed_action_and_reports_it_indeterminate(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A composite action that is not valid YAML is kept with its error."""
        _write(tmp_path, ".github/actions/setup/action.yml", "runs: {using: [\n")

        envelope = build_whitaker_provisioning_envelope(tmp_path)
        result = runner.run_rule(RULE_ID, tmp_path)

        (fact,) = typ.cast("list[dict[str, object]]", envelope["actions"])
        assert typ.cast("str", fact["error"]).startswith("invalid YAML")
        assert result.verdict == "indeterminate", result

    def test_leaves_out_an_oversized_binary(self, tmp_path: pathlib.Path) -> None:
        """A large compiled tool is a binary, not an unreadable script."""
        binary = tmp_path / "bin" / "act"
        binary.parent.mkdir()
        binary.write_bytes(b"\x7fELF\x00" + b"x" * MAX_SCRIPT_BYTES)

        envelope = build_whitaker_provisioning_envelope(tmp_path)

        assert _paths(envelope, "scripts") == []

    def test_leaves_out_test_code(self, tmp_path: pathlib.Path) -> None:
        """A test stubs the download it guards against; it is not a route."""
        _write(tmp_path, ".github/actions/tool/tests/test_fetch.py", "curl\n")
        _write(tmp_path, "scripts/test_install.py", "curl\n")
        _write(tmp_path, "scripts/install_test.py", "curl\n")

        envelope = build_whitaker_provisioning_envelope(tmp_path)

        assert _paths(envelope, "scripts") == []

    @pytest.mark.parametrize(
        "relative",
        ["scripts/setup.js", "tools/setup.rb", "ci/setup.pl", "bin/setup.ts"],
    )
    def test_reads_a_script_in_any_common_interpreter(
        self, tmp_path: pathlib.Path, relative: str
    ) -> None:
        """A workflow may run `node scripts/setup.js`, so its suffix is read."""
        _write(tmp_path, relative, "curl\n")

        envelope = build_whitaker_provisioning_envelope(tmp_path)

        assert _paths(envelope, "scripts") == [relative]

    def test_reads_a_script_with_an_unlisted_suffix_by_its_shebang(
        self, tmp_path: pathlib.Path
    ) -> None:
        """An interpreter the suffix list omits is still named by `#!`."""
        _write(tmp_path, "scripts/setup.lua", "#!/usr/bin/env lua\nprint(1)\n")
        _write(tmp_path, "scripts/notes.txt", "plain prose\n")

        envelope = build_whitaker_provisioning_envelope(tmp_path)

        assert _paths(envelope, "scripts") == ["scripts/setup.lua"]

    @pytest.mark.skipif(
        os.name == "nt" or os.geteuid() == 0,
        reason="a directory mode only denies listing to an unprivileged POSIX user",
    )
    def test_refuses_a_directory_it_cannot_list(self, tmp_path: pathlib.Path) -> None:
        """An unlistable directory is an error, not a silently skipped subtree."""
        _write(tmp_path, "scripts/hidden/install.sh", "curl\n")
        hidden = tmp_path / "scripts" / "hidden"
        hidden.chmod(0)
        try:
            with pytest.raises(OperationalRuleError, match="cannot list"):
                build_whitaker_provisioning_envelope(tmp_path)
        finally:
            hidden.chmod(0o700)


class TestRepositoryName:
    """Cover the slug the exemption clauses match on."""

    def test_reads_the_origin_slug(self, tmp_path: pathlib.Path) -> None:
        """A GitHub origin names the repository."""
        repository = pygit2.init_repository(str(tmp_path))
        repository.remotes.create("origin", "git@github.com:leynos/whitaker.git")

        envelope = build_whitaker_provisioning_envelope(tmp_path)

        assert envelope["repository"]["name"] == "leynos/whitaker"

    def test_has_no_name_without_an_origin(self, tmp_path: pathlib.Path) -> None:
        """No origin means no name, so no exemption can match."""
        pygit2.init_repository(str(tmp_path))

        envelope = build_whitaker_provisioning_envelope(tmp_path)

        assert envelope["repository"]["name"] is None

    def test_has_no_name_when_git_cannot_discover_the_repository(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A corrupt `.git` is a checkout without a name, not a raw git error."""

        def refuse(_path: str) -> str:
            """Fail as git does on a corrupt `.git` directory."""
            message = "corrupt repository"
            raise pygit2.GitError(message)

        monkeypatch.setattr(pygit2, "discover_repository", refuse)

        envelope = build_whitaker_provisioning_envelope(tmp_path)

        assert envelope["repository"]["name"] is None


class TestRuleRun:
    """Run the package through the real runner and Conftest."""

    def test_a_hand_rolled_install_is_noncompliant(
        self, tmp_path: pathlib.Path
    ) -> None:
        """The shipped policy refuses a binstall route."""
        _write(tmp_path, ".github/workflows/ci.yml", BINSTALL_WORKFLOW)

        result = runner.run_rule(RULE_ID, tmp_path)

        assert result.verdict == "noncompliant", result
        assert {finding.rule_id for finding in result.findings} == {"QG-002"}

    def test_the_first_listed_revision_is_compliant(
        self, tmp_path: pathlib.Path
    ) -> None:
        """The shipped parameters accept the revision they list first."""
        parameters = packages.rule_parameters(packages.rule_package_dir(RULE_ID))
        refs = typ.cast("list[str]", parameters["compliant_install_whitaker_refs"])
        _write(
            tmp_path,
            ".github/workflows/ci.yml",
            "on: pull_request\njobs:\n  lint:\n    runs-on: ubuntu-latest\n"
            "    steps:\n      - uses: leynos/shared-actions/.github/actions/"
            f"install-whitaker@{refs[0]}\n",
        )

        result = runner.run_rule(RULE_ID, tmp_path)

        assert result.verdict == "compliant", result

    @pytest.mark.parametrize(
        ("makefile", "verdict"),
        [
            pytest.param(
                "lint:\n\techo done # cargo install whitaker-installer\n",
                "compliant",
                id="trailing-comment-is-prose",
            ),
            pytest.param(
                "lint:\n\tcargo install whitaker-installer # the old way\n",
                "noncompliant",
                id="command-before-a-comment-is-a-route",
            ),
            pytest.param(
                "lint:\n\techo 'a # b'; cargo install whitaker-installer\n",
                "noncompliant",
                id="hash-inside-quotes-hides-nothing",
            ),
            pytest.param(
                "lint:\n\techo it's; cargo install whitaker-installer\n",
                "noncompliant",
                id="lone-apostrophe-hides-nothing",
            ),
        ],
    )
    def test_a_trailing_comment_is_prose_only_outside_quotes(
        self, tmp_path: pathlib.Path, makefile: str, verdict: str
    ) -> None:
        """A `#` after whitespace outside quotes ends the command it follows."""
        _write(tmp_path, "Makefile", makefile)

        result = runner.run_rule(RULE_ID, tmp_path)

        assert result.verdict == verdict, result

    def test_a_download_in_a_javascript_script_is_noncompliant(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A script CI runs with `node` is audited like a shell script."""
        _write(
            tmp_path,
            "scripts/fetch.js",
            "fetch('https://example.invalid/whitaker-installer'); // curl\n",
        )
        _write(tmp_path, "scripts/fetch2.js", "run('curl whitaker-installer')\n")

        result = runner.run_rule(RULE_ID, tmp_path)

        assert result.verdict == "noncompliant", result

    @pytest.mark.parametrize(
        ("selector", "verdict"),
        [
            pytest.param(-1, "compliant", id="newest-listed-revision"),
            pytest.param("a" * 39 + "5", "noncompliant", id="unlisted-revision"),
            pytest.param(
                "a5765019912a8ab6882b12db049c7cde635f3a85",
                "noncompliant",
                id="revision-before-the-install-rules",
            ),
        ],
    )
    def test_only_a_derived_revision_is_accepted(
        self, tmp_path: pathlib.Path, selector: int | str, verdict: str
    ) -> None:
        """A pin is compliant exactly when the derived list names it."""
        parameters = packages.rule_parameters(packages.rule_package_dir(RULE_ID))
        refs = typ.cast("list[str]", parameters["compliant_install_whitaker_refs"])
        ref = refs[selector] if isinstance(selector, int) else selector
        _write(
            tmp_path,
            ".github/workflows/ci.yml",
            "on: pull_request\njobs:\n  lint:\n    runs-on: ubuntu-latest\n"
            "    steps:\n      - uses: leynos/shared-actions/.github/actions/"
            f"install-whitaker@{ref}\n",
        )

        result = runner.run_rule(RULE_ID, tmp_path)

        assert result.verdict == verdict, result


@pytest.mark.parametrize("surface", ["workflows", "actions", "scripts"])
def test_an_empty_checkout_has_empty_surfaces(
    tmp_path: pathlib.Path, surface: str
) -> None:
    """A repository without automation is a subject with nothing to refuse."""
    envelope = build_whitaker_provisioning_envelope(tmp_path)

    assert _paths(envelope, surface) == []
