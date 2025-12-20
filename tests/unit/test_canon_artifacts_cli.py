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


def _setup_multi_artifact_cli_scenario(
    tmp_path: Path,
    artifacts: list[tuple[str, str, str, str, str | None]],
) -> tuple[Path, Path]:
    template_root = tmp_path / "concordat"
    published_root = tmp_path / "platform-standards-published"
    manifest_path = template_root / "platform-standards" / "canon" / "manifest.yaml"

    entries: list[dict[str, str]] = []
    for (
        artifact_id,
        artifact_type,
        relative_path,
        template_content,
        published_content,
    ) in artifacts:
        template_file = _write_template_file(
            template_root, relative_path, template_content
        )
        sha = hashlib.sha256(template_file.read_bytes()).hexdigest()
        entries.append(
            {
                "id": artifact_id,
                "type": artifact_type,
                "path": relative_path,
                "description": artifact_id,
                "sha256": sha,
            }
        )

        if published_content is None:
            continue

        published_relpath = Path(relative_path)
        if published_relpath.parts[:1] == ("platform-standards",):
            published_relpath = published_relpath.relative_to("platform-standards")
        published_file = published_root / published_relpath
        published_file.parent.mkdir(parents=True, exist_ok=True)
        published_file.write_text(published_content, encoding="utf-8")

    _write_manifest_entries(manifest_path=manifest_path, entries=entries)
    return template_root, published_root


@pytest.fixture
def template_root_with_simple_manifest(tmp_path: Path) -> Path:
    """Provide a template root with a single artifact manifest."""
    template_root = tmp_path / "concordat"
    _write_template_and_manifest(template_root=template_root, content="rule = 1\n")
    return template_root


@pytest.fixture
def published_root(tmp_path: Path) -> Path:
    """Provide an empty published root directory."""
    return tmp_path / "platform-standards-published"


def _invoke_app_and_capture(
    args: list[str],
    capsys: pytest.CaptureFixture[str],
) -> tuple[int, str]:
    """Invoke the canon_artifacts app and return (exit_code, output)."""
    exit_code = int(canon_artifacts.app(args, result_action="return_value") or 0)
    output = capsys.readouterr().out
    return exit_code, output


def test_status_via_app_reports_missing_and_exit_code(
    template_root_with_simple_manifest: Path,
    published_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Status prints a table and returns exit codes based on flags."""
    exit_code, output = _invoke_app_and_capture(
        [
            "status",
            str(published_root),
            "--template-root",
            str(template_root_with_simple_manifest),
            "--fail-on-missing",
        ],
        capsys,
    )
    assert "python-ruff-config" in output
    assert "missing" in output
    assert exit_code == 2


def test_status_via_app_outdated_only_filters_ok(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--outdated-only omits OK rows from the status output."""
    template_root, published_root = _setup_multi_artifact_cli_scenario(
        tmp_path,
        [
            (
                "ok",
                "lint-config",
                "platform-standards/canon/lint/python/ok.toml",
                "ok = true\n",
                "ok = true\n",
            ),
            (
                "missing",
                "lint-config",
                "platform-standards/canon/lint/python/missing.toml",
                "missing = true\n",
                None,
            ),
        ],
    )

    exit_code, output = _invoke_app_and_capture(
        [
            "status",
            str(published_root),
            "--template-root",
            str(template_root),
            "--outdated-only",
        ],
        capsys,
    )
    assert "missing" in output
    assert "ok" not in output
    assert exit_code == 0


def test_status_via_app_exit_code_3_on_template_manifest_mismatch(
    tmp_path: Path,
    published_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Status returns exit code 3 when the template manifest is stale."""
    template_root = tmp_path / "concordat"
    _write_template_and_manifest(
        template_root=template_root,
        content="rule = 1\n",
        sha_override="0" * 64,
    )

    exit_code, output = _invoke_app_and_capture(
        [
            "status",
            str(published_root),
            "--template-root",
            str(template_root),
        ],
        capsys,
    )
    assert "template-manifest-mismatch" in output
    assert exit_code == 3


def test_status_via_app_respects_types_filter(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Status honours the --types filter."""
    template_root, published_root = _setup_multi_artifact_cli_scenario(
        tmp_path,
        [
            (
                "lint",
                "lint-config",
                "platform-standards/canon/lint/python/ruff.toml",
                "rule = 1\n",
                None,
            ),
            (
                "policy",
                "opa-policy",
                "platform-standards/canon/policies/workflows/test.rego",
                "package test\n",
                None,
            ),
        ],
    )

    exit_code, output = _invoke_app_and_capture(
        [
            "status",
            str(published_root),
            "--template-root",
            str(template_root),
            "--types",
            "opa-policy",
        ],
        capsys,
    )
    assert "policy" in output
    assert "lint" not in output
    assert exit_code == 0


def test_sync_via_app_copies_file(
    template_root_with_simple_manifest: Path,
    published_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Sync copies missing artifacts into the published checkout."""
    exit_code, output = _invoke_app_and_capture(
        [
            "sync",
            str(published_root),
            "python-ruff-config",
            "--template-root",
            str(template_root_with_simple_manifest),
        ],
        capsys,
    )
    assert exit_code == 0
    assert "copied" in output

    published_file = published_root / "canon" / "lint" / "python" / "ruff.toml"
    assert published_file.read_text(encoding="utf-8") == "rule = 1\n"


def test_sync_via_app_accepts_relative_published_root(
    template_root_with_simple_manifest: Path,
    published_root: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sync accepts a relative published-root path without crashing."""
    monkeypatch.chdir(published_root.parent)
    relative_published_root = Path("platform-standards-published")

    exit_code, output = _invoke_app_and_capture(
        [
            "sync",
            str(relative_published_root),
            "python-ruff-config",
            "--template-root",
            str(template_root_with_simple_manifest),
        ],
        capsys,
    )
    assert exit_code == 0
    assert "copied" in output

    published_file = published_root / "canon" / "lint" / "python" / "ruff.toml"
    assert published_file.read_text(encoding="utf-8") == "rule = 1\n"


def test_sync_dry_run_does_not_create_file(
    template_root_with_simple_manifest: Path,
    published_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--dry-run reports actions but does not write to disk."""
    exit_code, output = _invoke_app_and_capture(
        [
            "sync",
            str(published_root),
            "python-ruff-config",
            "--template-root",
            str(template_root_with_simple_manifest),
            "--dry-run",
        ],
        capsys,
    )
    assert exit_code == 0
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

    try:
        with pytest.raises(canon_artifacts.CanonArtifactsError) as excinfo:
            canon_artifacts.tui(
                published_root,
                template_root=template_root,
                ids=(),
                types=(),
            )

        assert "Textual is required for `tui`" in str(excinfo.value)
    finally:
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
