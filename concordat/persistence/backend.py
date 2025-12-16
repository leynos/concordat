"""Backend configuration for OpenTofu remote state.

This module handles the configuration of remote state backends for OpenTofu,
including credential resolution from environment variables (AWS, Scaleway,
DigitalOcean Spaces) and validation of backend configuration paths.
"""

from __future__ import annotations

import os
import typing as typ
from pathlib import Path  # noqa: TC003

from concordat.errors import ConcordatError
from concordat.persistence import models as persistence_models

if typ.TYPE_CHECKING:
    from concordat.persistence.models import PersistenceDescriptor

# Environment variable tuples for different S3-compatible backends.
AWS_BACKEND_ENV = ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY")
SCW_BACKEND_ENV = ("SCW_ACCESS_KEY", "SCW_SECRET_KEY")
SPACES_BACKEND_ENV = (
    "SPACES_ACCESS_KEY_ID",
    "SPACES_SECRET_ACCESS_KEY",
)
AWS_SESSION_TOKEN_VAR = "AWS_SESSION_TOKEN"  # noqa: S105

# All backend environment variables for iteration.
ALL_BACKEND_ENV_VARS = (
    AWS_BACKEND_ENV + SCW_BACKEND_ENV + SPACES_BACKEND_ENV + (AWS_SESSION_TOKEN_VAR,)
)

# Error messages.
ERROR_BACKEND_ENV_MISSING = (
    "Remote state backend requires credentials in the environment: either "
    "AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY, SCW_ACCESS_KEY and "
    "SCW_SECRET_KEY, or SPACES_ACCESS_KEY_ID and SPACES_SECRET_ACCESS_KEY."
)
ERROR_BACKEND_CONFIG_MISSING = (
    "Remote backend config {path!r} was not found in the estate workspace."
)
ERROR_BACKEND_PATH_OUTSIDE = (
    "Remote backend config must live inside the estate workspace (got {path})."
)


class BackendConfigurationError(ConcordatError):
    """Raised when backend configuration is invalid or incomplete."""


def session_token_overrides(env: typ.Mapping[str, str]) -> dict[str, str]:
    """Return a mapping containing AWS session token when present and non-empty.

    Args:
        env: Environment mapping to check for session token.

    Returns:
        Dict with AWS_SESSION_TOKEN if present and non-empty, empty dict otherwise.

    """
    token = env.get(AWS_SESSION_TOKEN_VAR, "").strip()
    return {AWS_SESSION_TOKEN_VAR: token} if token else {}


def remove_blank_session_token(env: dict[str, str]) -> None:
    """Normalize AWS session token, removing it when blank and trimming whitespace.

    Args:
        env: Environment dict to modify in-place.

    """
    token = env.get(AWS_SESSION_TOKEN_VAR)
    if token is None:
        return
    stripped = token.strip()
    if stripped:
        env[AWS_SESSION_TOKEN_VAR] = stripped
        return
    env.pop(AWS_SESSION_TOKEN_VAR, None)


def resolve_backend_environment(env: typ.Mapping[str, str]) -> dict[str, str]:
    """Return env overrides for tofu, erroring when credentials are missing.

    Checks for credentials in order of preference: AWS, Scaleway, then Spaces.
    Maps non-AWS credentials to the AWS environment variable names that tofu
    expects.

    Args:
        env: Environment mapping to search for credentials.

    Returns:
        Dict with AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY set.

    Raises:
        BackendConfigurationError: If no valid credentials are found.

    """

    def present(*names: str) -> bool:
        return all(env.get(name, "").strip() for name in names)

    if present(*AWS_BACKEND_ENV):
        return {
            "AWS_ACCESS_KEY_ID": env["AWS_ACCESS_KEY_ID"].strip(),
            "AWS_SECRET_ACCESS_KEY": env["AWS_SECRET_ACCESS_KEY"].strip(),
            **session_token_overrides(env),
        }

    if present(*SCW_BACKEND_ENV):
        return {
            "AWS_ACCESS_KEY_ID": env["SCW_ACCESS_KEY"].strip(),
            "AWS_SECRET_ACCESS_KEY": env["SCW_SECRET_KEY"].strip(),
            **session_token_overrides(env),
        }

    if present(*SPACES_BACKEND_ENV):
        return {
            "AWS_ACCESS_KEY_ID": env["SPACES_ACCESS_KEY_ID"].strip(),
            "AWS_SECRET_ACCESS_KEY": env["SPACES_SECRET_ACCESS_KEY"].strip(),
            **session_token_overrides(env),
        }

    raise BackendConfigurationError(ERROR_BACKEND_ENV_MISSING)


def validate_backend_path(workdir: Path, backend_config_path: str) -> Path:
    """Validate backend config path is inside workspace and exists.

    Args:
        workdir: The workspace root directory.
        backend_config_path: Relative path to the backend config file.

    Returns:
        The validated relative path to the backend config file.

    Raises:
        BackendConfigurationError: If the path escapes the workspace or is missing.

    """
    backend_path = (workdir / backend_config_path).resolve()
    workdir_resolved = workdir.resolve()

    try:
        relative_backend = backend_path.relative_to(workdir_resolved)
    except ValueError as error:
        message = ERROR_BACKEND_PATH_OUTSIDE.format(path=backend_config_path)
        raise BackendConfigurationError(message) from error

    if not backend_path.is_file():
        message = ERROR_BACKEND_CONFIG_MISSING.format(path=backend_config_path)
        raise BackendConfigurationError(message)

    return relative_backend


def build_object_key(descriptor: PersistenceDescriptor) -> str:
    """Construct the full S3 object key from prefix and suffix.

    Args:
        descriptor: Persistence descriptor with key_prefix and key_suffix.

    Returns:
        The full S3 object key path.

    """
    prefix = descriptor.key_prefix.rstrip("/")
    suffix = descriptor.key_suffix.lstrip("/")
    return f"{prefix}/{suffix}" if prefix else suffix


def get_persistence_runtime(
    workspace_root: Path,
    tofu_workdir: Path,
    env: typ.Mapping[str, str],
) -> tuple[
    persistence_models.PersistenceDescriptor | None,
    str | None,
    str | None,
    dict[str, str] | None,
]:
    """Load the persistence manifest and derive backend runtime details.

    Args:
        workspace_root: Root directory of the estate workspace.
        tofu_workdir: Directory containing tofu configuration.
        env: Environment mapping for credential resolution.

    Returns:
        A tuple of (descriptor, backend_config, object_key, env_overrides).
        Returns (None, None, None, None) if persistence is disabled or missing.

    Raises:
        BackendConfigurationError: If the manifest is invalid or credentials
            are missing.

    """
    manifest_path = workspace_root / persistence_models.MANIFEST_FILENAME
    try:
        descriptor = persistence_models.PersistenceDescriptor.from_yaml(manifest_path)
    except persistence_models.PersistenceError as error:
        raise BackendConfigurationError(str(error)) from error

    if descriptor is None or not descriptor.enabled:
        return None, None, None, None

    relative_backend = validate_backend_path(
        workspace_root,
        descriptor.backend_config_path,
    )
    backend_path = (workspace_root / relative_backend).resolve()
    tofu_root = tofu_workdir.resolve()
    backend_config = os.path.relpath(backend_path, tofu_root)
    env_overrides = resolve_backend_environment(env)
    object_key = build_object_key(descriptor)

    return descriptor, backend_config, object_key, env_overrides
