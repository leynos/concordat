"""Validation of user inputs and remote S3 backends."""
# ruff: noqa: TRY003

from __future__ import annotations

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
        message = f"Failed to query bucket versioning: {error}"
        raise PersistenceError(message) from error
    except boto_exceptions.ClientError as error:  # type: ignore[attr-defined]
        message = f"Failed to query bucket versioning: {error}"
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


def _default_s3_client_factory(region: str, endpoint: str) -> S3Client:
    """Create a boto3 S3 client configured for path-style endpoints."""
    config = BotoConfig(s3={"addressing_style": "path"})
    return typ.cast(
        "S3Client",
        boto3.client(
            "s3",
            region_name=region,
            endpoint_url=endpoint,
            config=config,
        ),
    )
