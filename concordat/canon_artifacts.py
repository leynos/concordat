"""Compare and sync canonical platform-standards artifacts.

This module treats `platform-standards/canon/manifest.yaml` as the source of truth
for canonical artifacts shipped in the Concordat repository, and can compare them
against a checked-out (published) platform-standards repository.
"""

# ruff: noqa: TRY003

from __future__ import annotations

import dataclasses
import enum
import hashlib
import shutil
import typing as typ
from pathlib import Path

from ruamel.yaml import YAML

from .errors import ConcordatError

_yaml = YAML(typ="safe")

DEFAULT_MANIFEST_RELATIVE = Path("platform-standards/canon/manifest.yaml")
_PUBLISHED_PREFIX = ("platform-standards",)


class CanonArtifactsError(ConcordatError):
    """Raised when canonical artifact operations fail."""


class ArtifactStatus(enum.StrEnum):
    """High-level status for an artifact against the published checkout."""

    OK = "ok"
    MISSING = "missing"
    OUTDATED = "outdated"
    TEMPLATE_MANIFEST_MISMATCH = "template-manifest-mismatch"


@dataclasses.dataclass(frozen=True, slots=True)
class CanonArtifact:
    """Single artifact entry from the canonical manifest."""

    id: str
    type: str
    path: Path
    description: str
    sha256: str

    def template_path(self, *, template_root: Path) -> Path:
        """Return the absolute path to the artifact in the template tree."""
        return template_root / self.path

    def published_relpath(self) -> Path:
        """Return the artifact relative path inside the published repo checkout."""
        parts = self.path.parts
        if parts[: len(_PUBLISHED_PREFIX)] == _PUBLISHED_PREFIX:
            return Path(*parts[len(_PUBLISHED_PREFIX) :])
        return self.path

    def published_path(self, *, published_root: Path) -> Path:
        """Return the absolute path to the artifact in the published tree."""
        return published_root / self.published_relpath()


@dataclasses.dataclass(frozen=True, slots=True)
class CanonManifest:
    """Parsed canonical manifest plus helper metadata."""

    schema_version: int
    artifacts: tuple[CanonArtifact, ...]
    manifest_path: Path

    @property
    def template_root(self) -> Path:
        """Return the Concordat checkout root containing the manifest."""
        return self.manifest_path.parent.parent.parent


@dataclasses.dataclass(frozen=True, slots=True)
class ArtifactComparison:
    """Comparison between template and published artifact."""

    artifact: CanonArtifact
    template_sha256: str
    published_sha256: str | None
    status: ArtifactStatus
    published_path: Path

    @property
    def id(self) -> str:
        """Artifact identifier as listed in the manifest."""
        return self.artifact.id

    @property
    def type(self) -> str:
        """Artifact type (for example `lint-config`, `workflow`, `opa-policy`)."""
        return self.artifact.type

    @property
    def manifest_sha256(self) -> str:
        """sha256 digest recorded in the template manifest."""
        return self.artifact.sha256

    @property
    def template_relpath(self) -> Path:
        """Template-relative path as recorded in the manifest."""
        return self.artifact.path


@dataclasses.dataclass(frozen=True, slots=True)
class SyncAction:
    """A single copy action performed (or proposed) during sync."""

    artifact_id: str
    source: Path
    destination: Path
    status: ArtifactStatus
    copied: bool


def resolve_concordat_root(start: Path | None = None) -> Path:
    """Find the Concordat checkout root by locating the canonical manifest."""
    cursor = (start or Path.cwd()).resolve()
    for candidate in (cursor, *cursor.parents):
        if (candidate / DEFAULT_MANIFEST_RELATIVE).exists():
            return candidate
    raise CanonArtifactsError(
        "Unable to locate platform-standards template tree. "
        f"Expected to find {DEFAULT_MANIFEST_RELATIVE} in a parent directory; "
        "pass --template-root explicitly."
    )


def load_manifest(manifest_path: Path) -> CanonManifest:
    """Load and validate the canonical artifact manifest."""
    if not manifest_path.exists():
        raise CanonArtifactsError(f"Manifest not found: {manifest_path}")
    data = _yaml.load(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise CanonArtifactsError(
            f"Manifest content must be a mapping: {manifest_path}"
        )
    schema_version = data.get("schema_version")
    if schema_version != 1:
        raise CanonArtifactsError(
            f"Unsupported manifest schema_version={schema_version!r} "
            f"(expected 1): {manifest_path}"
        )
    artifacts_raw = data.get("artifacts", [])
    if not isinstance(artifacts_raw, list) or not artifacts_raw:
        raise CanonArtifactsError(
            f"Manifest artifacts must be a non-empty list: {manifest_path}"
        )

    artifacts: list[CanonArtifact] = []
    for entry in artifacts_raw:
        if not isinstance(entry, dict):
            raise CanonArtifactsError(
                f"Manifest artifact entries must be mappings: {manifest_path}"
            )
        try:
            artifacts.append(
                CanonArtifact(
                    id=str(entry["id"]),
                    type=str(entry["type"]),
                    path=Path(str(entry["path"])),
                    description=str(entry["description"]),
                    sha256=str(entry["sha256"]),
                )
            )
        except KeyError as exc:
            raise CanonArtifactsError(
                f"Manifest artifact missing key {exc.args[0]!r}: {manifest_path}"
            ) from exc

    return CanonManifest(
        schema_version=schema_version,
        artifacts=tuple(artifacts),
        manifest_path=manifest_path,
    )


def sha256_digest(path: Path) -> str:
    """Compute a deterministic sha256 digest for a file or directory."""
    if path.is_file():
        return hashlib.sha256(path.read_bytes()).hexdigest()
    if not path.is_dir():
        raise CanonArtifactsError(f"Expected file or directory, got: {path}")

    hasher = hashlib.sha256()
    for file_path in sorted(p for p in path.rglob("*") if p.is_file()):
        rel = file_path.relative_to(path).as_posix().encode("utf-8")
        hasher.update(rel)
        hasher.update(b"\0")
        hasher.update(file_path.read_bytes())
        hasher.update(b"\0")
    return hasher.hexdigest()


def compare_manifest_to_published(
    manifest: CanonManifest,
    *,
    published_root: Path,
    ids: set[str] | None = None,
    types: set[str] | None = None,
) -> tuple[ArtifactComparison, ...]:
    """Compare template artifacts against a published platform-standards checkout."""
    published_root = published_root.resolve()
    comparisons: list[ArtifactComparison] = []
    for artifact in manifest.artifacts:
        if ids is not None and artifact.id not in ids:
            continue
        if types is not None and artifact.type not in types:
            continue

        template_path = artifact.template_path(template_root=manifest.template_root)
        template_sha = sha256_digest(template_path)
        published_path = artifact.published_path(published_root=published_root)
        if not published_path.exists():
            published_sha: str | None = None
        else:
            published_sha = sha256_digest(published_path)

        status = _resolve_status(
            template_sha256=template_sha,
            manifest_sha256=artifact.sha256,
            published_sha256=published_sha,
        )
        comparisons.append(
            ArtifactComparison(
                artifact=artifact,
                template_sha256=template_sha,
                published_sha256=published_sha,
                status=status,
                published_path=published_path,
            )
        )
    return tuple(comparisons)


def _resolve_status(
    *,
    template_sha256: str,
    manifest_sha256: str,
    published_sha256: str | None,
) -> ArtifactStatus:
    if template_sha256 != manifest_sha256:
        return ArtifactStatus.TEMPLATE_MANIFEST_MISMATCH
    if published_sha256 is None:
        return ArtifactStatus.MISSING
    if published_sha256 != template_sha256:
        return ArtifactStatus.OUTDATED
    return ArtifactStatus.OK


def sync_artifacts(
    comparisons: typ.Iterable[ArtifactComparison],
    *,
    template_root: Path,
    published_root: Path,
    ids: set[str] | None = None,
    dry_run: bool = False,
    include_unchanged: bool = False,
) -> tuple[SyncAction, ...]:
    """Copy template artifacts into the published checkout."""
    published_root = published_root.resolve()
    actions: list[SyncAction] = []
    for comparison in comparisons:
        if ids is not None and comparison.id not in ids:
            continue
        if comparison.status == ArtifactStatus.OK and not include_unchanged:
            continue
        if comparison.status == ArtifactStatus.TEMPLATE_MANIFEST_MISMATCH:
            continue

        source = template_root / comparison.template_relpath
        destination = comparison.published_path
        copied = False
        if not dry_run:
            destination.parent.mkdir(parents=True, exist_ok=True)
            if source.is_dir():
                shutil.copytree(source, destination, dirs_exist_ok=True)
            else:
                shutil.copy2(source, destination)
            copied = True
        actions.append(
            SyncAction(
                artifact_id=comparison.id,
                source=source,
                destination=destination,
                status=comparison.status,
                copied=copied,
            )
        )
    return tuple(actions)


def render_status_table(comparisons: typ.Sequence[ArtifactComparison]) -> str:
    """Render a fixed-width table for comparisons."""
    headers = ("id", "type", "status", "template", "published", "path")
    rows: list[tuple[str, ...]] = [headers]

    for comparison in comparisons:
        template = comparison.template_sha256[:12]
        published = (comparison.published_sha256 or "-")[:12]
        rows.append(
            (
                comparison.id,
                comparison.type,
                str(comparison.status),
                template,
                published,
                comparison.artifact.published_relpath().as_posix(),
            )
        )

    widths = [max(len(row[i]) for row in rows) for i in range(len(headers))]
    lines: list[str] = []
    for idx, row in enumerate(rows):
        parts = [cell.ljust(widths[i]) for i, cell in enumerate(row)]
        line = "  ".join(parts)
        lines.append(line)
        if idx == 0:
            lines.append("  ".join("-" * width for width in widths))
    return "\n".join(lines)
