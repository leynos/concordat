"""Behavioural tests for the canon_artifacts CLI wrapper."""

from __future__ import annotations

import dataclasses
import hashlib
import sys
import typing as typ
from pathlib import Path

import pytest

from concordat.canon_artifacts import compare_manifest_to_published, load_manifest
from scripts import canon_artifacts

if typ.TYPE_CHECKING:
    import types


def _write_template_and_manifest(
    *,
    template_root: Path,
    content: str,
    sha_override: str | None = None,
) -> None:
    template_file = (
        template_root / "platform-standards" / "canon" / "lint" / "python" / "ruff.toml"
    )
    template_file.parent.mkdir(parents=True, exist_ok=True)
    template_file.write_text(content, encoding="utf-8")
    sha = sha_override or hashlib.sha256(template_file.read_bytes()).hexdigest()

    manifest_path = template_root / "platform-standards" / "canon" / "manifest.yaml"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        "\n".join(
            [
                "schema_version: 1",
                "artifacts:",
                "  - id: python-ruff-config",
                "    type: lint-config",
                "    path: platform-standards/canon/lint/python/ruff.toml",
                "    description: test artifact",
                f"    sha256: {sha}",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _write_manifest_entries(
    *,
    manifest_path: Path,
    entries: list[dict[str, str]],
) -> None:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = ["schema_version: 1", "artifacts:"]
    for entry in entries:
        lines.extend(
            [
                f"  - id: {entry['id']}",
                f"    type: {entry['type']}",
                f"    path: {entry['path']}",
                f"    description: {entry['description']}",
                f"    sha256: {entry['sha256']}",
            ]
        )
    lines.append("")
    manifest_path.write_text("\n".join(lines), encoding="utf-8")


def _write_template_file(template_root: Path, relative_path: str, content: str) -> Path:
    path = template_root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_status_via_app_reports_missing_and_exit_code(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Status prints a table and returns exit codes based on flags."""
    template_root = tmp_path / "concordat"
    _write_template_and_manifest(template_root=template_root, content="rule = 1\n")

    published_root = tmp_path / "platform-standards-published"
    exit_code = int(
        canon_artifacts.app(
            [
                "status",
                str(published_root),
                "--template-root",
                str(template_root),
                "--fail-on-missing",
            ],
            result_action="return_value",
        )
        or 0
    )
    output = capsys.readouterr().out
    assert "python-ruff-config" in output
    assert "missing" in output
    assert exit_code == 2


def test_status_via_app_outdated_only_filters_ok(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """--outdated-only omits OK rows from the status output."""
    template_root = tmp_path / "concordat"
    ok_template = _write_template_file(
        template_root,
        "platform-standards/canon/lint/python/ok.toml",
        "ok = true\n",
    )
    missing_template = _write_template_file(
        template_root,
        "platform-standards/canon/lint/python/missing.toml",
        "missing = true\n",
    )
    manifest_path = template_root / "platform-standards" / "canon" / "manifest.yaml"
    _write_manifest_entries(
        manifest_path=manifest_path,
        entries=[
            {
                "id": "ok",
                "type": "lint-config",
                "path": "platform-standards/canon/lint/python/ok.toml",
                "description": "ok",
                "sha256": hashlib.sha256(ok_template.read_bytes()).hexdigest(),
            },
            {
                "id": "missing",
                "type": "lint-config",
                "path": "platform-standards/canon/lint/python/missing.toml",
                "description": "missing",
                "sha256": hashlib.sha256(missing_template.read_bytes()).hexdigest(),
            },
        ],
    )

    published_root = tmp_path / "platform-standards-published"
    published_ok = published_root / "canon" / "lint" / "python" / "ok.toml"
    published_ok.parent.mkdir(parents=True, exist_ok=True)
    published_ok.write_text("ok = true\n", encoding="utf-8")

    exit_code = int(
        canon_artifacts.app(
            [
                "status",
                str(published_root),
                "--template-root",
                str(template_root),
                "--outdated-only",
            ],
            result_action="return_value",
        )
        or 0
    )
    output = capsys.readouterr().out
    assert "missing" in output
    assert "ok" not in output
    assert exit_code == 0


def test_status_via_app_exit_code_3_on_template_manifest_mismatch(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Status returns exit code 3 when the template manifest is stale."""
    template_root = tmp_path / "concordat"
    _write_template_and_manifest(
        template_root=template_root,
        content="rule = 1\n",
        sha_override="0" * 64,
    )
    published_root = tmp_path / "platform-standards-published"

    exit_code = int(
        canon_artifacts.app(
            [
                "status",
                str(published_root),
                "--template-root",
                str(template_root),
            ],
            result_action="return_value",
        )
        or 0
    )
    output = capsys.readouterr().out
    assert "template-manifest-mismatch" in output
    assert exit_code == 3


def test_status_via_app_respects_types_filter(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Status honours the --types filter."""
    template_root = tmp_path / "concordat"
    lint_template = _write_template_file(
        template_root,
        "platform-standards/canon/lint/python/ruff.toml",
        "rule = 1\n",
    )
    policy_template = _write_template_file(
        template_root,
        "platform-standards/canon/policies/workflows/test.rego",
        "package test\n",
    )

    manifest_path = template_root / "platform-standards" / "canon" / "manifest.yaml"
    _write_manifest_entries(
        manifest_path=manifest_path,
        entries=[
            {
                "id": "lint",
                "type": "lint-config",
                "path": "platform-standards/canon/lint/python/ruff.toml",
                "description": "lint",
                "sha256": hashlib.sha256(lint_template.read_bytes()).hexdigest(),
            },
            {
                "id": "policy",
                "type": "opa-policy",
                "path": "platform-standards/canon/policies/workflows/test.rego",
                "description": "policy",
                "sha256": hashlib.sha256(policy_template.read_bytes()).hexdigest(),
            },
        ],
    )

    published_root = tmp_path / "platform-standards-published"
    exit_code = int(
        canon_artifacts.app(
            [
                "status",
                str(published_root),
                "--template-root",
                str(template_root),
                "--types",
                "opa-policy",
            ],
            result_action="return_value",
        )
        or 0
    )
    output = capsys.readouterr().out
    assert "policy" in output
    assert "lint" not in output
    assert exit_code == 0


def test_sync_via_app_copies_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Sync copies missing artifacts into the published checkout."""
    template_root = tmp_path / "concordat"
    _write_template_and_manifest(template_root=template_root, content="rule = 1\n")

    published_root = tmp_path / "platform-standards-published"
    result = canon_artifacts.app(
        [
            "sync",
            str(published_root),
            "python-ruff-config",
            "--template-root",
            str(template_root),
        ],
        result_action="return_value",
    )
    assert int(result or 0) == 0
    output = capsys.readouterr().out
    assert "copied" in output

    published_file = published_root / "canon" / "lint" / "python" / "ruff.toml"
    assert published_file.read_text(encoding="utf-8") == "rule = 1\n"


def test_sync_via_app_accepts_relative_published_root(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sync accepts a relative published-root path without crashing."""
    template_root = tmp_path / "concordat"
    _write_template_and_manifest(template_root=template_root, content="rule = 1\n")

    published_root = tmp_path / "platform-standards-published"
    monkeypatch.chdir(tmp_path)
    relative_published_root = Path("platform-standards-published")

    result = canon_artifacts.app(
        [
            "sync",
            str(relative_published_root),
            "python-ruff-config",
            "--template-root",
            str(template_root),
        ],
        result_action="return_value",
    )
    assert int(result or 0) == 0
    output = capsys.readouterr().out
    assert "copied" in output

    published_file = published_root / "canon" / "lint" / "python" / "ruff.toml"
    assert published_file.read_text(encoding="utf-8") == "rule = 1\n"


def test_sync_dry_run_does_not_create_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """--dry-run reports actions but does not write to disk."""
    template_root = tmp_path / "concordat"
    _write_template_and_manifest(template_root=template_root, content="rule = 1\n")

    published_root = tmp_path / "platform-standards-published"
    result = canon_artifacts.app(
        [
            "sync",
            str(published_root),
            "python-ruff-config",
            "--template-root",
            str(template_root),
            "--dry-run",
        ],
        result_action="return_value",
    )
    assert int(result or 0) == 0
    output = capsys.readouterr().out
    assert "would copy" in output

    published_file = published_root / "canon" / "lint" / "python" / "ruff.toml"
    assert published_file.exists() is False


def test_list_artifacts_prints_registered_rows(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """list_artifacts prints the manifest entries."""
    template_root = tmp_path / "concordat"
    _write_template_and_manifest(template_root=template_root, content="rule = 1\n")

    canon_artifacts.list_artifacts(template_root=template_root, types=())
    output = capsys.readouterr().out
    assert "python-ruff-config" in output
    assert "lint-config" in output


def test_main_returns_1_on_command_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Main catches CanonArtifactsError and exits with code 1."""
    template_root = tmp_path / "concordat"
    _write_template_and_manifest(template_root=template_root, content="rule = 1\n")
    published_root = tmp_path / "platform-standards-published"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "canon_artifacts.py",
            "sync",
            str(published_root),
            "--template-root",
            str(template_root),
        ],
    )
    exit_code = canon_artifacts.main()
    output = capsys.readouterr().out
    assert exit_code == 1
    assert "No artifacts selected" in output


@dataclasses.dataclass
class _BlockModuleFinder:
    """Block imports for a specific module prefix."""

    prefix: str

    def find_spec(self, fullname: str, path: object, target: object) -> None:
        """Raise ModuleNotFoundError for the configured prefix."""
        if fullname.startswith(self.prefix):
            raise ModuleNotFoundError(fullname)


def test_tui_raises_when_textual_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tui raises a prescriptive error when textual cannot be imported."""
    template_root = tmp_path / "concordat"
    _write_template_and_manifest(template_root=template_root, content="rule = 1\n")
    published_root = tmp_path / "platform-standards-published"

    finder = _BlockModuleFinder(prefix="textual")
    monkeypatch.setattr(sys, "meta_path", [finder, *sys.meta_path])
    removed: dict[str, types.ModuleType] = {}
    for name in list(sys.modules):
        if name == "scripts.canon_artifacts_tui" or name.startswith("textual"):
            removed[name] = sys.modules.pop(name)

    with pytest.raises(canon_artifacts.CanonArtifactsError) as excinfo:
        canon_artifacts.tui(
            published_root,
            template_root=template_root,
            ids=(),
            types=(),
        )

    assert "Textual is required for `tui`" in str(excinfo.value)
    sys.modules.update(removed)


def test_compute_status_exit_code_matrix(tmp_path: Path) -> None:
    """_compute_status_exit_code returns expected codes for common inputs."""
    template_root = tmp_path / "concordat"
    _write_template_and_manifest(template_root=template_root, content="rule = 1\n")
    manifest = load_manifest(
        template_root / "platform-standards" / "canon" / "manifest.yaml"
    )
    published_root = tmp_path / "platform-standards-published"

    missing = list(
        compare_manifest_to_published(manifest, published_root=published_root)
    )
    assert (
        canon_artifacts._compute_status_exit_code(
            missing, fail_on_outdated=False, fail_on_missing=False
        )
        == 0
    )
    assert (
        canon_artifacts._compute_status_exit_code(
            missing, fail_on_outdated=False, fail_on_missing=True
        )
        == 2
    )

    published_file = published_root / "canon" / "lint" / "python" / "ruff.toml"
    published_file.parent.mkdir(parents=True, exist_ok=True)
    published_file.write_text("rule = 2\n", encoding="utf-8")
    outdated = list(
        compare_manifest_to_published(manifest, published_root=published_root)
    )
    assert (
        canon_artifacts._compute_status_exit_code(
            outdated, fail_on_outdated=True, fail_on_missing=False
        )
        == 2
    )


def test_determine_sync_ids_all_outdated_no_matches_is_noop(tmp_path: Path) -> None:
    """_determine_sync_ids treats --all-outdated with no matches as a no-op."""
    config = canon_artifacts.CliSyncConfig(
        published_root=tmp_path / "published",
        artifact_ids=(),
        template_root=None,
        types=(),
        dry_run=False,
        all_outdated=True,
        include_unchanged=False,
    )
    assert canon_artifacts._determine_sync_ids(config, []) == set()
