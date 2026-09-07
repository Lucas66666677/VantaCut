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
    """Whether the *internal* object-storage endpoint was configured.

    A pure comparison against the development fallback -- no network, no boto3,
    no credential read -- so it is safe to call on the hot path of every
    upload-issuing request. This is the endpoint the backend itself reaches:
    `head_bucket`, `complete_multipart_upload`, and the `object_exists` check
    behind `confirm-upload` all dial `settings.s3_endpoint_url`.
    """
    return settings.s3_endpoint_url != DEVELOPMENT_S3_ENDPOINT


def public_storage_is_configured() -> bool:
    """Whether the *browser-facing* object-storage endpoint was configured.

    Every presigned URL a client actually uses -- the upload PUT, each
    multipart part, and the artifact download GET -- is signed against
    `settings.s3_public_endpoint_url` (see app/services/storage.py), which the
    browser dials directly. It is a distinct host from the internal endpoint by
    design: `.env.production.example` pairs `S3_ENDPOINT_URL=https://s3...` with
    `S3_PUBLIC_ENDPOINT_URL=https://media...`.

    The trap is that it *defaults to the internal endpoint*. Checking only that
    the value is non-local is not enough: when the internal endpoint is a real
    private host (a VPC address, a compose service name) and
    `S3_PUBLIC_ENDPOINT_URL` was never set, the inherited value is non-local and
    still unreachable from a browser. So in production the browser endpoint must
    be declared *explicitly* -- an inherited default reads as unconfigured,
    however real the host it was inherited from looks. Outside production the
    single-host fallback (public == internal) is intentional and kept, as long
    as it is not the `localhost:9000` development default.

    Pure comparison, like `storage_is_configured`: safe on the request hot path.
    """
    if settings.s3_public_endpoint_url == DEVELOPMENT_S3_ENDPOINT:
        return False
    if settings.environment == "production" and not settings.s3_public_endpoint_url_explicit:
        # Inherited the internal endpoint by default; in production that host is
        # not assumed to be browser-reachable, so it must be stated on purpose.
        return False
    return True


def storage_readiness() -> dict[str, bool]:
    configured = storage_is_configured()
    public_configured = public_storage_is_configured()
    reachable = _probe_bucket() if configured else False
    return {
        "configured": configured,
        "public_endpoint_configured": public_configured,
        "bucket_reachable": reachable,
        # The browser upload/download path needs the internal endpoint reachable
        # *and* the public endpoint configured. Either one missing breaks the
        # end-to-end flow, so both gate the single headline signal.
        "uploads_expected_to_work": configured and public_configured and reachable,
    }
