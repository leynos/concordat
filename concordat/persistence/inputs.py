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
        "key_suffix": DEFAULT_KEY_FILENAME,
    }


def _collect_user_inputs(
    defaults: dict[str, str],
    input_func: typ.Callable[[str], str],
) -> dict[str, str]:
    """Gather bucket, region, endpoint, and key values from the user."""
    return {
        "bucket": _prompt_with_default("Bucket", defaults["bucket"], input_func),
        "region": _prompt_with_default("Region", defaults["region"], input_func),
        "endpoint": _prompt_with_default("Endpoint", defaults["endpoint"], input_func),
        "key_prefix": _prompt_with_default(
            "Key prefix", defaults["key_prefix"], input_func
        ),
        "key_suffix": _prompt_with_default(
            "Key suffix", defaults["key_suffix"], input_func
        ),
    }


def _prompt_with_default(
    label: str,
    default: str,
    input_func: typ.Callable[[str], str],
) -> str:
    """Prompt with a default value and enforce non-empty responses."""
    suffix = f" [{default}]" if default else ""
    response = input_func(f"{label}{suffix}: ").strip()
    if response:
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
        region=prompts["region"],
        endpoint=prompts["endpoint"],
        backend_config_path=str(Path(BACKEND_DIRNAME) / backend_path.name),
    )
