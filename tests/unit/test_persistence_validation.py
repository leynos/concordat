"""Validation helpers for persistence inputs and S3 checks."""

from __future__ import annotations

import typing as typ

import pytest
from botocore import exceptions as boto_exceptions

import concordat.persistence.validation as persistence_validation
from concordat import persistence
from concordat.persistence import S3Client


@pytest.mark.parametrize(
    ("bucket", "region", "endpoint", "message"),
    [
        ("", "fr-par", "https://s3.fr-par.scw.cloud", "Bucket is required."),
        ("df12", "", "https://s3.fr-par.scw.cloud", "Region is required."),
        ("df12", "fr-par", "", "Endpoint is required."),
        (
            "df12",
            "fr-par",
            "s3.fr-par.scw.cloud",
            "Endpoint must include an https:// scheme",
        ),
        (
            "df12",
            "fr-par",
            "http://endpoint",
            "Endpoint must use HTTPS",
        ),
        (
            "df12",
            "fr-par",
            "https://endpoint",
            "",
        ),
    ],
)
def test_validate_inputs_enforces_constraints(
    bucket: str,
    region: str,
    endpoint: str,
    message: str,
) -> None:
    """Input validation blocks missing or insecure settings."""
    descriptor = persistence.PersistenceDescriptor(
        schema_version=persistence.PERSISTENCE_SCHEMA_VERSION,
        enabled=True,
        bucket=bucket,
        key_prefix="estates/example/main",
        key_suffix="terraform.tfstate",
        region=region,
        endpoint=endpoint,
        backend_config_path="backend/core.tfbackend",
    )
    key_suffix = "terraform.tfstate"
    if message:
        with pytest.raises(persistence.PersistenceError, match=message):
            persistence_validation._validate_inputs(descriptor, key_suffix)
    else:
        persistence_validation._validate_inputs(descriptor, key_suffix)


@pytest.mark.parametrize(
    ("key_prefix", "key_suffix", "expected_message"),
    [
        ("foo/../bar", "terraform.tfstate", "directory traversals"),
        ("estates/example/main", "   ", "Key suffix is required."),
    ],
    ids=["path_traversal_in_prefix", "empty_key_suffix"],
)
def test_validate_inputs_rejects_invalid_paths(
    key_prefix: str,
    key_suffix: str,
    expected_message: str,
) -> None:
    """Path validation blocks directory traversal and empty key suffix."""
    descriptor = persistence.PersistenceDescriptor(
        schema_version=persistence.PERSISTENCE_SCHEMA_VERSION,
        enabled=True,
        bucket="df12-tfstate",
        key_prefix=key_prefix,
        key_suffix="terraform.tfstate",
        region="fr-par",
        endpoint="https://s3.fr-par.scw.cloud",
        backend_config_path="backend/core.tfbackend",
    )
    with pytest.raises(persistence.PersistenceError) as excinfo:
        persistence_validation._validate_inputs(descriptor, key_suffix)

    assert expected_message in str(excinfo.value)


def test_validate_inputs_allows_insecure_endpoint_when_opted_in() -> None:
    """Insecure endpoints are permitted when explicitly allowed."""
    descriptor = persistence.PersistenceDescriptor(
        schema_version=persistence.PERSISTENCE_SCHEMA_VERSION,
        enabled=True,
        bucket="df12-tfstate",
        key_prefix="estates/example/main",
        key_suffix="terraform.tfstate",
        region="fr-par",
        endpoint="http://localhost:9000",
        backend_config_path="backend/core.tfbackend",
    )
    persistence_validation._validate_inputs(
        descriptor,
        "terraform.tfstate",
        allow_insecure_endpoint=True,
    )


@pytest.mark.parametrize(
    "exception_factory",
    [
        lambda: boto_exceptions.BotoCoreError(),
        lambda: boto_exceptions.ClientError(  # type: ignore[arg-type]
            error_response={
                "Error": {
                    "Code": "AccessDenied",
                    "Message": "Access denied while getting bucket versioning",
                }
            },
            operation_name="GetBucketVersioning",
        ),
    ],
)
def test_bucket_versioning_status_wraps_errors(
    exception_factory: typ.Callable[[], Exception],
) -> None:
    """Versioning failures surface as PersistenceError."""

    class Client(S3Client):
        def get_bucket_versioning(self, **kwargs: object) -> dict[str, str]:
            raise exception_factory()

    client = Client()
    with pytest.raises(persistence.PersistenceError) as excinfo:
        persistence_validation._bucket_versioning_status(client, "bucket")
    assert "Versioning check failed" in str(excinfo.value)


@pytest.mark.parametrize(
    ("failing_operation", "test_description"),
    [
        ("put", "write probe"),
        ("delete", "delete probe"),
    ],
    ids=["put_object_fails", "delete_object_fails"],
)
def test_exercise_write_permissions_wraps_errors(
    failing_operation: str,
    test_description: str,
) -> None:
    """Write/delete probe failures become PersistenceError."""

    class Client(S3Client):
        def put_object(self, **kwargs: object) -> dict[str, str]:
            if failing_operation == "put":
                exc = boto_exceptions.BotoCoreError()
                raise exc
            return {}

        def delete_object(self, **kwargs: object) -> dict[str, str]:
            if failing_operation == "delete":
                exc = boto_exceptions.BotoCoreError()
                raise exc
            return {}

    client = Client()
    with pytest.raises(persistence.PersistenceError) as excinfo:
        persistence_validation._exercise_write_permissions(client, "bucket", "key")
    message = str(excinfo.value)
    assert "Bucket permissions" in message
    assert "failed" in message
