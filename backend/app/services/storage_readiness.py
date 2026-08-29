"""Whether object storage is actually usable by this deployment.

Kept out of app/services/storage.py, and importing boto3 lazily, so this module
stays importable by the CI test slice that deliberately does not install boto3
(see tests/conftest.py).
"""

from app.core.config import settings

# app/core/config.py's development fallback. Matching it means nobody ever set
# S3_ENDPOINT_URL, i.e. the deployment is pointed at a local MinIO that is not
# there.
DEVELOPMENT_S3_ENDPOINT = "http://localhost:9000"


def _probe_bucket() -> bool:
    """One short, non-retrying HeadBucket. Any failure means "not usable"."""
    try:
        import boto3
        from botocore.client import Config

        client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region,
            # A health probe must not inherit boto3's default retry/timeout
            # budget; it has to answer fast or answer "no".
            config=Config(
                signature_version="s3v4",
                connect_timeout=2,
                read_timeout=2,
                retries={"max_attempts": 1},
            ),
        )
        client.head_bucket(Bucket=settings.s3_bucket)
        return True
    except Exception:  # noqa: BLE001 - the reason must not reach an unauthenticated caller
        return False


def storage_is_configured() -> bool:
    """Whether a real object-storage endpoint was configured for this deploy.

    A pure comparison against the development fallback -- no network, no boto3,
    no credential read -- so it is safe to call on the hot path of every
    upload-issuing request. `storage_readiness()` layers a bucket probe on top
    for the readiness endpoint; the upload endpoints want only this cheap half,
    because handing a client a presigned URL that points at a MinIO nobody
    started is a failure the API can refuse up front rather than one the
    browser discovers against a dead `localhost:9000`.
    """
    return settings.s3_endpoint_url != DEVELOPMENT_S3_ENDPOINT


def storage_readiness() -> dict[str, bool]:
    configured = storage_is_configured()
    reachable = _probe_bucket() if configured else False
    return {
        "configured": configured,
        "bucket_reachable": reachable,
        "uploads_expected_to_work": configured and reachable,
    }
