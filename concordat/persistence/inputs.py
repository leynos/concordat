"""User input collection and descriptor construction."""
# ruff: noqa: TRY003

from __future__ import annotations

import typing as typ
from pathlib import Path

from .models import (
    BACKEND_DIRNAME,
    DEFAULT_KEY_FILENAME,
    PERSISTENCE_SCHEMA_VERSION,
    PersistenceDescriptor,
    PersistenceError,
)

if typ.TYPE_CHECKING:
    from concordat.estate import EstateRecord


def _defaults_from(
    record: EstateRecord,
    descriptor: PersistenceDescriptor | None,
) -> dict[str, str]:
    """Populate prompt defaults from existing state."""
    owner = record.github_owner or "unknown-owner"
    base_prefix = f"estates/{owner}/{record.branch}"
    return {
        "bucket": descriptor.bucket if descriptor else "",
        "region": descriptor.region if descriptor else "",
        "endpoint": descriptor.endpoint if descriptor else "",
        "key_prefix": descriptor.key_prefix if descriptor else base_prefix,
        "key_suffix": descriptor.key_suffix if descriptor else DEFAULT_KEY_FILENAME,
    }


def _collect_user_inputs(
    defaults: dict[str, str],
    input_func: typ.Callable[[str], str],
    preset: dict[str, str],
    *,
    allow_prompt: bool,
) -> dict[str, str]:
    """Gather bucket, region, endpoint, and key values from the user."""
    labels = {
        "bucket": "Bucket",
        "region": "Region",
        "endpoint": "Endpoint",
        "key_prefix": "Key prefix",
        "key_suffix": "Key suffix",
    }
    responses: dict[str, str] = {}
    for field, label in labels.items():
        preset_value = preset.get(field, "").strip()
        if preset_value:
            responses[field] = preset_value
            continue
        default = defaults[field]
        if not allow_prompt:
            if default:
                responses[field] = default
                continue
            raise PersistenceError(
                f"{label} is required in non-interactive mode; provide a flag or "
                f"environment variable."
            )
        responses[field] = _prompt_with_default(label, default, input_func)
    return responses


def _prompt_with_default(
    label: str,
    default: str,
    input_func: typ.Callable[[str], str],
) -> str:
    """Prompt with a default value and enforce non-empty responses."""
    suffix = f" [{default}]" if default else ""
    if response := input_func(f"{label}{suffix}: ").strip():
        return response
    if default:
        return default
    raise PersistenceError(f"{label} is required.")


def _build_descriptor(
    prompts: dict[str, str],
    backend_path: Path,
) -> PersistenceDescriptor:
    """Construct a descriptor from collected prompt values."""
    return PersistenceDescriptor(
        schema_version=PERSISTENCE_SCHEMA_VERSION,
        enabled=True,
        bucket=prompts["bucket"],
        key_prefix=prompts["key_prefix"],
        key_suffix=prompts["key_suffix"],
        region=prompts["region"],
        endpoint=prompts["endpoint"],
        backend_config_path=str(Path(BACKEND_DIRNAME) / backend_path.name),
    )
