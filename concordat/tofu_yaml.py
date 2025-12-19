"""YAML sanitization utilities for OpenTofu compatibility.

OpenTofu's `yamldecode()` function uses a YAML parser that rejects certain
valid YAML constructs, including `%YAML` directives and explicit document
markers (`---` and `...`). This module provides functions to strip these
unsupported elements from YAML files before they are processed by tofu.
"""

from __future__ import annotations

from pathlib import Path

# YAML markers that tofu's yamldecode cannot parse.
_YAML_DIRECTIVE_PREFIX = "%YAML"
_YAML_DOCUMENT_START = "---"
_YAML_DOCUMENT_END = "..."
_UTF8_BOM = "\ufeff"

# Directory name for tofu configuration within estate repositories.
TOFU_DIRNAME = "tofu"


def strip_yaml_directives_for_tofu(contents: str) -> tuple[str, bool]:
    """Remove YAML directives and document markers unsupported by tofu yamldecode.

    OpenTofu/Terraform `yamldecode()` uses a YAML parser that rejects some YAML
    directives, in particular the `%YAML 1.2` header and explicit document
    markers. Concordat historically wrote these into the inventory file, which
    causes `plan`/`apply` to fail even though the YAML is valid in other tools.

    This function performs a minimal, surgical rewrite that only strips markers
    at the beginning/end of the file so we do not rewrite the entire document.

    Returns:
        A tuple of (sanitized_contents, changed) where changed is True if any
        modifications were made.

    """
    if not contents:
        return contents, False

    changed = False
    text = contents
    if text.startswith(_UTF8_BOM):
        text = text.lstrip(_UTF8_BOM)
        changed = True

    lines = text.splitlines()
    if not lines:
        return text, changed

    index = 0
    while index < len(lines) and not lines[index].strip():
        index += 1

    while index < len(lines) and lines[index].lstrip().startswith(
        _YAML_DIRECTIVE_PREFIX
    ):
        changed = True
        index += 1
        while index < len(lines) and not lines[index].strip():
            index += 1

    if index < len(lines) and lines[index].strip() == _YAML_DOCUMENT_START:
        changed = True
        index += 1

    stripped_lines = lines[index:]

    tail_index = len(stripped_lines) - 1
    while tail_index >= 0 and not stripped_lines[tail_index].strip():
        tail_index -= 1
    if tail_index >= 0 and stripped_lines[tail_index].strip() == _YAML_DOCUMENT_END:
        changed = True
        stripped_lines = stripped_lines[:tail_index]

    normalized = "\n".join(stripped_lines)
    if normalized and not normalized.endswith("\n"):
        normalized += "\n"

    if normalized == contents:
        return contents, False
    return normalized, changed or normalized != contents


def sanitize_yaml_file_for_tofu(path: Path) -> bool:
    """Strip tofu-incompatible YAML markers from the file at path.

    Args:
        path: Path to a YAML file to sanitize in-place.

    Returns:
        True if the file was modified, False otherwise.

    """
    try:
        contents = path.read_text(encoding="utf-8")
    except OSError:
        return False

    sanitized, changed = strip_yaml_directives_for_tofu(contents)
    if not changed:
        return False

    path.write_text(sanitized, encoding="utf-8")
    return True


def _build_inventory_candidates(
    workspace_root: Path,
    tofu_workdir: Path,
    inventory_path: str,
) -> list[Path]:
    """Build list of candidate inventory file paths to sanitize.

    Returns paths in both workspace root and tofu working directory,
    adjusting the inventory path if it starts with the tofu dirname.
    """
    candidates: list[Path] = [workspace_root / inventory_path]
    if tofu_workdir.resolve() != workspace_root.resolve():
        relative = Path(inventory_path)
        if relative.parts and relative.parts[0] == TOFU_DIRNAME:
            relative = Path(*relative.parts[1:])
        candidates.append(tofu_workdir / relative)
    return candidates


def _sanitize_candidates(candidates: list[Path]) -> bool:
    """Sanitize all existing inventory files in the candidates list.

    Returns True if any file was modified, False otherwise.
    """
    changed = False
    for candidate in candidates:
        if candidate.is_file():
            changed = sanitize_yaml_file_for_tofu(candidate) or changed
    return changed


def sanitize_inventory_for_tofu(
    workspace_root: Path,
    tofu_workdir: Path,
    inventory_path: str,
) -> bool:
    """Sanitise the inventory YAML in-place for tofu consumption.

    Checks both the workspace root and tofu working directory for inventory
    files that may need sanitization.

    Args:
        workspace_root: Root directory of the estate workspace.
        tofu_workdir: Directory containing tofu configuration.
        inventory_path: Relative path to the inventory file.

    Returns:
        True if any inventory file was modified, False otherwise.

    """
    candidates = _build_inventory_candidates(
        workspace_root, tofu_workdir, inventory_path
    )
    return _sanitize_candidates(candidates)
