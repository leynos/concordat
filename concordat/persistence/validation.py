"""Validation of user inputs and remote S3 backends."""
# ruff: noqa: TRY003

from __future__ import annotations

import os
import typing as typ

import boto3
from botocore import exceptions as boto_exceptions
from botocore.config import Config as BotoConfig

from .models import (
    PERSISTENCE_CHECK_SUFFIX,
    PersistenceDescriptor,
    PersistenceError,
    S3Client,
)

AWS_BACKEND_ENV = ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY")
SCW_BACKEND_ENV = ("SCW_ACCESS_KEY", "SCW_SECRET_KEY")
SPACES_BACKEND_ENV = (
    "SPACES_ACCESS_KEY_ID",
    "SPACES_SECRET_ACCESS_KEY",
)
AWS_SESSION_TOKEN_VAR = "AWS_SESSION_TOKEN"  # noqa: S105


def _validate_inputs(
    descriptor: PersistenceDescriptor,
    key_suffix: str,
    *,
    allow_insecure_endpoint: bool = False,
) -> None:
    """Validate descriptor fields and endpoint constraints."""
    _validate_path_safety(descriptor.key_prefix, "Key prefix")
    _validate_path_safety(key_suffix, "Key suffix")
    _validate_key_suffix_not_empty(key_suffix)
    _validate_required_fields(descriptor)
    _validate_endpoint_protocol(
        descriptor.endpoint,
        allow_insecure_endpoint=allow_insecure_endpoint,
    )


def _validate_path_safety(path: str, field_name: str) -> None:
    """Ensure path segments do not include traversal elements."""
    if ".." in path.split("/"):
        raise PersistenceError(f"{field_name} may not include directory traversals.")


def _validate_key_suffix_not_empty(key_suffix: str) -> None:
    """Ensure the key suffix is not empty or whitespace only."""
    if not key_suffix.strip():
        raise PersistenceError("Key suffix is required.")


def _validate_required_fields(descriptor: PersistenceDescriptor) -> None:
    """Ensure required descriptor fields are populated."""
    if not descriptor.bucket:
        raise PersistenceError("Bucket is required.")
    if not descriptor.region:
        raise PersistenceError("Region is required.")


def _check_endpoint_scheme(endpoint: str, *, allow_insecure: bool) -> None:
    """Validate the endpoint scheme, preserving existing error messaging."""
    if endpoint.startswith("https://"):
        return

    if allow_insecure and endpoint.startswith("http://"):
        return

    if "://" not in endpoint:
        raise PersistenceError(
            "Endpoint must include an https:// scheme (for example, "
            "https://s3.example.com)."
        )

    raise PersistenceError(
        "Endpoint must use HTTPS (for example, https://s3.example.com)."
    )


def _validate_endpoint_protocol(
    endpoint: str, *, allow_insecure_endpoint: bool = False
) -> None:
    """Ensure endpoints use HTTPS unless explicitly allowed for dev use."""
    if not (endpoint := endpoint.strip()):
        raise PersistenceError("Endpoint is required.")
    _check_endpoint_scheme(endpoint, allow_insecure=allow_insecure_endpoint)


def _validate_bucket(
    descriptor: PersistenceDescriptor,
    key_suffix: str,
    s3_client_factory: typ.Callable[[str, str], S3Client],
) -> None:
    """Validate bucket versioning and write/delete permissions."""
    client = s3_client_factory(descriptor.region, descriptor.endpoint)
    status = _bucket_versioning_status(client, descriptor.bucket)
    if status != "Enabled":
        message = (
            f"Bucket {descriptor.bucket!r} must enable versioning "
            f"(status: {status or 'unknown'})."
        )
        raise PersistenceError(message)
    _exercise_write_permissions(
        client,
        descriptor.bucket,
        f"{descriptor.key_prefix.rstrip('/')}/{key_suffix.lstrip('/')}",
    )


def _bucket_versioning_status(client: S3Client, bucket: str) -> str | None:
    """Return bucket versioning status or surface errors as PersistenceError."""
    try:
        response = client.get_bucket_versioning(Bucket=bucket)
    except boto_exceptions.BotoCoreError as error:
        if isinstance(error, boto_exceptions.NoCredentialsError):
            message = (
                "Versioning check failed: unable to locate S3 credentials. "
                "Export AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY, "
                "SCW_ACCESS_KEY/SCW_SECRET_KEY, or "
                "SPACES_ACCESS_KEY_ID/SPACES_SECRET_ACCESS_KEY, then retry. "
                f"Details: {error}"
            )
            raise PersistenceError(message) from error
        message = (
            "Versioning check failed: unable to reach the bucket API. "
            "Verify credentials, endpoint, and network connectivity, then retry. "
            f"Details: {error}"
        )
        raise PersistenceError(message) from error
    except boto_exceptions.ClientError as error:  # type: ignore[attr-defined]
        message = (
            "Versioning check failed: the bucket API rejected the request. "
            "Confirm the bucket exists and the provided credentials can query it. "
            f"Details: {error}"
        )
        raise PersistenceError(message) from error
    status = response.get("Status")
    return str(status) if status is not None else None


def _perform_s3_operation(
    operation: typ.Callable[[], typ.Any], error_message: str
) -> None:
    """Execute an S3 operation and convert exceptions to PersistenceError."""
    try:
        operation()
    except boto_exceptions.BotoCoreError as error:
        message = f"{error_message}: {error}"
        raise PersistenceError(message) from error
    except boto_exceptions.ClientError as error:  # type: ignore[attr-defined]
        message = f"{error_message}: {error}"
        raise PersistenceError(message) from error


def _exercise_write_permissions(client: S3Client, bucket: str, key: str) -> None:
    """Attempt to put/delete a probe object to confirm permissions."""
    probe_key = f"{key}.{PERSISTENCE_CHECK_SUFFIX}"
    _perform_s3_operation(
        lambda: client.put_object(Bucket=bucket, Key=probe_key, Body=b""),
        "Bucket permissions check failed",
    )
    _perform_s3_operation(
        lambda: client.delete_object(Bucket=bucket, Key=probe_key),
        "Bucket permissions cleanup failed after write probe",
    )


def _session_token_from_environment(env: typ.Mapping[str, str]) -> str | None:
    token = env.get(AWS_SESSION_TOKEN_VAR, "").strip()
    return token if token else None


def _credentials_from_environment(env: typ.Mapping[str, str]) -> dict[str, str]:
    """Resolve S3 credentials from supported environment variables.

    Boto3 recognises the AWS_* variables. Concordat also supports alternative
    names for S3-compatible vendors (Scaleway/Spaces) and maps those to the
    boto3 client arguments.
    """

    def present(*names: str) -> bool:
        return all(env.get(name, "").strip() for name in names)

    if present(*AWS_BACKEND_ENV):
        resolved = {
            "aws_access_key_id": env["AWS_ACCESS_KEY_ID"].strip(),
            "aws_secret_access_key": env["AWS_SECRET_ACCESS_KEY"].strip(),
        }
    elif present(*SCW_BACKEND_ENV):
        resolved = {
            "aws_access_key_id": env["SCW_ACCESS_KEY"].strip(),
            "aws_secret_access_key": env["SCW_SECRET_KEY"].strip(),
        }
    elif present(*SPACES_BACKEND_ENV):
        resolved = {
            "aws_access_key_id": env["SPACES_ACCESS_KEY_ID"].strip(),
            "aws_secret_access_key": env["SPACES_SECRET_ACCESS_KEY"].strip(),
        }
    else:
        return {}

    if token := _session_token_from_environment(env):
        resolved["aws_session_token"] = token
    return resolved


def _default_s3_client_factory(region: str, endpoint: str) -> S3Client:
    """Create a boto3 S3 client configured for path-style endpoints."""
    config = BotoConfig(s3={"addressing_style": "path"})
    credentials = _credentials_from_environment(os.environ)
    return typ.cast(
        "S3Client",
        boto3.client(
            "s3",
            region_name=region,
            endpoint_url=endpoint,
            config=config,
            **credentials,
        ),
    )
