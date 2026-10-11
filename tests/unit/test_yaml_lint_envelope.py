"""Specify the yaml-lint-baseline policy-input envelope."""

from __future__ import annotations

import hashlib
import typing as typ

import pytest

from concordat.errors import OperationalRuleError
from concordat.rules import packages
from concordat.rules.makefile_facts import MakefileFacts, MakeutilReport
from concordat.rules.yaml_lint_envelope import (
    ENVELOPE_KIND,
    ENVELOPE_SCHEMA_VERSION,
    DecodedFile,
    YamlLintEnvelope,
    build_yaml_lint_envelope,
)
from tests.unit.rule_test_support import MINIMAL_REPORT

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    import pathlib

RULE_ID: typ.Final = "yaml-lint-baseline"


def _write(checkout: pathlib.Path, relative: str, text: str) -> pathlib.Path:
    """Write *text* at *relative* under *checkout* and return the path."""
    path = checkout / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_an_empty_checkout_carries_no_facts(tmp_path: pathlib.Path) -> None:
    """Nothing to read is recorded as absence, never as a decoding error."""
    envelope = build_yaml_lint_envelope(tmp_path)

    assert envelope["schema_version"] == ENVELOPE_SCHEMA_VERSION
    assert envelope["kind"] == ENVELOPE_KIND
    assert envelope["makefile"] is None
    assert envelope["yamllint_base"] is None
    assert envelope["yamllint_local"] is None
    assert envelope["actionlint_config"] is None
    assert envelope["workflows"] == []
    assert envelope["alternate_yamllint_configs"] == []
    assert envelope["applicability"] == {
        "yaml_files": False,
        "workflows": False,
        "root_makefile": False,
    }


def test_yaml_anywhere_outside_pruned_directories_applies(
    tmp_path: pathlib.Path,
) -> None:
    """A nested YAML file makes the baseline apply; a vendored one does not."""
    _write(tmp_path, "node_modules/pkg/config.yml", "a: 1\n")
    _write(tmp_path, ".venv/lib/x.yaml", "a: 1\n")
    assert build_yaml_lint_envelope(tmp_path)["applicability"]["yaml_files"] is False

    _write(tmp_path, "deploy/charts/values.yaml", "a: 1\n")
    assert build_yaml_lint_envelope(tmp_path)["applicability"]["yaml_files"] is True


def test_a_symlinked_yaml_file_alone_does_not_apply(tmp_path: pathlib.Path) -> None:
    """The walk reads the link, never its target, as the Markdown walk does."""
    target = _write(tmp_path, "elsewhere.txt", "a: 1\n")
    (tmp_path / "link.yml").symlink_to(target)

    assert build_yaml_lint_envelope(tmp_path)["applicability"]["yaml_files"] is False


def test_the_base_file_is_recorded_by_its_digest(tmp_path: pathlib.Path) -> None:
    """The baseline wants the canonical bytes, so the envelope carries a hash."""
    text = "---\nextends: default\n"
    _write(tmp_path, ".yamllint-estate.yml", text)

    base = build_yaml_lint_envelope(tmp_path)["yamllint_base"]

    assert base == {
        "path": ".yamllint-estate.yml",
        "sha256": hashlib.sha256(text.encode()).hexdigest(),
        "error": None,
    }


def test_a_non_utf8_base_file_still_has_a_digest(tmp_path: pathlib.Path) -> None:
    """Undecodable bytes are a mismatch to report, not an absence."""
    (tmp_path / ".yamllint-estate.yml").write_bytes(b"\xff\xfe")

    base = build_yaml_lint_envelope(tmp_path)["yamllint_base"]

    assert base is not None
    assert base["sha256"] == hashlib.sha256(b"\xff\xfe").hexdigest()


def test_the_local_config_is_decoded(tmp_path: pathlib.Path) -> None:
    """The local file's keys reach the policy as decoded values."""
    _write(
        tmp_path, ".yamllint.yml", "extends: .yamllint-estate.yml\nignore: |\n  x/\n"
    )

    local = build_yaml_lint_envelope(tmp_path)["yamllint_local"]

    assert local == {
        "path": ".yamllint.yml",
        "parsed": {"extends": ".yamllint-estate.yml", "ignore": "x/\n"},
        "error": None,
    }


def _local_config(envelope: YamlLintEnvelope) -> DecodedFile | None:
    """Return the envelope's local yamllint fact."""
    return envelope["yamllint_local"]


def _actionlint_config(envelope: YamlLintEnvelope) -> DecodedFile | None:
    """Return the envelope's actionlint fact."""
    return envelope["actionlint_config"]


@pytest.mark.parametrize(
    ("name", "select"),
    [
        (".yamllint.yml", _local_config),
        (".github/actionlint.yaml", _actionlint_config),
    ],
    ids=["yamllint", "actionlint"],
)
@pytest.mark.parametrize(
    "damage",
    [(b"a: [\n", "invalid YAML"), (b"\xff\xfe", "not UTF-8")],
    ids=["invalid YAML", "not UTF-8"],
)
def test_an_undecodable_config_keeps_its_reason(
    tmp_path: pathlib.Path,
    name: str,
    select: cabc.Callable[[YamlLintEnvelope], DecodedFile | None],
    damage: tuple[bytes, str],
) -> None:
    """A content failure is evidence for an indeterminate verdict, not absence."""
    content, reason = damage
    (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / name).write_bytes(content)

    fact = select(build_yaml_lint_envelope(tmp_path))

    assert fact is not None
    assert fact["parsed"] is None
    assert reason in str(fact["error"]), fact


def test_a_failed_read_does_not_spoil_the_next_one(tmp_path: pathlib.Path) -> None:
    """Each configuration is read with a fresh parser."""
    _write(tmp_path, ".yamllint.yml", "a: [\n")
    _write(tmp_path, ".github/actionlint.yaml", "self-hosted-runner:\n  labels: [a]\n")

    envelope = build_yaml_lint_envelope(tmp_path)

    assert _local_config(envelope) is not None
    assert (_actionlint_config(envelope) or {}).get("error") is None
    assert (_actionlint_config(envelope) or {}).get("parsed") == {
        "self-hosted-runner": {"labels": ["a"]}
    }


def test_a_date_mapping_key_is_a_configuration_error(tmp_path: pathlib.Path) -> None:
    """JSON cannot carry a date key; the audit records it rather than crashing."""
    _write(tmp_path, ".yamllint.yml", "2001-01-01: first\n")

    local = _local_config(build_yaml_lint_envelope(tmp_path))

    assert local is not None
    assert local["parsed"] is None
    assert "unsupported mapping key" in str(local["error"]), local


def test_alternate_yamllint_names_are_listed(tmp_path: pathlib.Path) -> None:
    """Yamllint would honour these, so the policy names them in its finding."""
    _write(tmp_path, ".yamllint", "extends: default\n")
    _write(tmp_path, ".yamllint.yaml", "extends: default\n")

    envelope = build_yaml_lint_envelope(tmp_path)

    assert envelope["alternate_yamllint_configs"] == [
        ".yamllint",
        ".yamllint.yaml",
    ]
    assert envelope["yamllint_local"] is None


def test_a_repository_dot_config_directory_is_not_a_yamllint_config(
    tmp_path: pathlib.Path,
) -> None:
    """Yamllint reads `.config` only under the user's home, never the project."""
    _write(tmp_path, ".config/yamllint/config", "extends: default\n")
    _write(tmp_path, ".yamllint.yml", "extends: .yamllint-estate.yml\n")

    envelope = build_yaml_lint_envelope(tmp_path)

    assert envelope["alternate_yamllint_configs"] == []


def test_duplicate_keys_in_the_local_config_are_rejected(
    tmp_path: pathlib.Path,
) -> None:
    """The safe YAML loader refuses duplicate mapping keys, as the others do."""
    _write(tmp_path, ".yamllint.yml", "ignore: first/\nignore: second/\n")

    local = build_yaml_lint_envelope(tmp_path)["yamllint_local"]

    assert local is not None
    assert local["parsed"] is None
    assert local["error"] is not None


def test_actionlint_config_accepts_either_spelling(tmp_path: pathlib.Path) -> None:
    """Actionlint reads `.yaml` and `.yml`, and the `.yaml` file wins."""
    _write(tmp_path, ".github/actionlint.yml", "self-hosted-runner:\n  labels: [a]\n")
    only_yml = build_yaml_lint_envelope(tmp_path)["actionlint_config"]
    assert only_yml is not None
    assert only_yml["path"] == ".github/actionlint.yml"

    _write(tmp_path, ".github/actionlint.yaml", "self-hosted-runner:\n  labels: [b]\n")
    both = build_yaml_lint_envelope(tmp_path)["actionlint_config"]
    assert both is not None
    assert both["path"] == ".github/actionlint.yaml"
    assert both["parsed"] == {"self-hosted-runner": {"labels": ["b"]}}


def test_workflows_are_decoded_and_flag_the_workflows_applicability(
    tmp_path: pathlib.Path,
) -> None:
    """Workflows are read as the other envelopes read them, `on` staying a string."""
    _write(tmp_path, ".github/workflows/ci.yml", "on: push\njobs: {}\n")

    envelope = build_yaml_lint_envelope(tmp_path)

    assert [workflow["path"] for workflow in envelope["workflows"]] == [
        ".github/workflows/ci.yml"
    ]
    assert envelope["workflows"][0]["parsed"] == {"on": "push", "jobs": {}}
    assert envelope["applicability"]["workflows"] is True


def test_the_makefile_report_is_forwarded(tmp_path: pathlib.Path) -> None:
    """The envelope carries makeutil's report for the policy to reason over."""
    _write(tmp_path, "Makefile", "lint:\n\ttrue\n")
    seen: list[object] = []

    def inspect(path: pathlib.Path) -> MakefileFacts:
        seen.append(path)
        return MakefileFacts(
            report=typ.cast("MakeutilReport", MINIMAL_REPORT), status="complete"
        )

    envelope = build_yaml_lint_envelope(tmp_path, inspect=inspect)

    assert envelope["makefile"] == MINIMAL_REPORT
    assert envelope["applicability"]["root_makefile"] is True
    assert seen == [tmp_path / "Makefile"]


@pytest.mark.parametrize(
    "name",
    [
        ".yamllint-estate.yml",
        ".yamllint.yml",
        ".github/actionlint.yaml",
        ".github/actionlint.yml",
        "Makefile",
    ],
)
def test_a_linked_file_outside_the_checkout_is_refused(
    tmp_path: pathlib.Path, name: str
) -> None:
    """A policy input is never read from outside the audited tree."""
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = _write(outside, "secret.txt", "extends: default\n")
    checkout = tmp_path / "checkout"
    (checkout / name).parent.mkdir(parents=True)
    (checkout / name).symlink_to(secret)

    with pytest.raises(OperationalRuleError, match="outside the checkout"):
        build_yaml_lint_envelope(checkout)


def test_the_package_is_registered_with_its_builder() -> None:
    """The runner finds the builder by package identifier and by envelope kind."""
    assert RULE_ID in packages.PACKAGE_ENVELOPE_BUILDERS
    assert ENVELOPE_KIND in packages.INPUT_KIND_ENVELOPE_BUILDERS
