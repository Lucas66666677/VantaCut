import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from app.core.config import settings
from app.services.non_destructive import assert_not_original_overwrite


def _client(endpoint_url: str):
    return boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name=settings.s3_region,
        config=Config(signature_version="s3v4"),
    )


def create_upload_url(storage_key: str, content_type: str) -> str:
    return _client(settings.s3_public_endpoint_url).generate_presigned_url(
        ClientMethod="put_object",
        Params={
            "Bucket": settings.s3_bucket,
            "Key": storage_key,
            "ContentType": content_type,
        },
        ExpiresIn=settings.presigned_url_expire_seconds,
        HttpMethod="PUT",
    )


def create_multipart_upload(storage_key: str, content_type: str) -> str:
    response = _client(settings.s3_endpoint_url).create_multipart_upload(
        Bucket=settings.s3_bucket, Key=storage_key, ContentType=content_type
    )
    return str(response["UploadId"])


def create_multipart_part_url(storage_key: str, upload_id: str, part_number: int) -> str:
    return _client(settings.s3_public_endpoint_url).generate_presigned_url(
        ClientMethod="upload_part",
        Params={"Bucket": settings.s3_bucket, "Key": storage_key, "UploadId": upload_id, "PartNumber": part_number},
        ExpiresIn=settings.presigned_url_expire_seconds,
        HttpMethod="PUT",
    )


def complete_multipart_upload(storage_key: str, upload_id: str, parts: list[dict[str, object]]) -> None:
    _client(settings.s3_endpoint_url).complete_multipart_upload(
        Bucket=settings.s3_bucket, Key=storage_key, UploadId=upload_id,
        MultipartUpload={"Parts": parts},
    )


def object_exists(storage_key: str) -> bool:
    try:
        _client(settings.s3_endpoint_url).head_object(
            Bucket=settings.s3_bucket, Key=storage_key
        )
        return True
    except Exception:
        return False


def download_object(storage_key: str, destination: str) -> None:
    _client(settings.s3_endpoint_url).download_file(
        settings.s3_bucket, storage_key, destination
    )


def upload_object(storage_key: str, source: str, content_type: str) -> None:
    assert_not_original_overwrite(storage_key)
    _client(settings.s3_endpoint_url).upload_file(
        source,
        settings.s3_bucket,
        storage_key,
        ExtraArgs={"ContentType": content_type},
    )


def upload_bytes(storage_key: str, payload: bytes, content_type: str) -> None:
    assert_not_original_overwrite(storage_key)
    _client(settings.s3_endpoint_url).put_object(
        Bucket=settings.s3_bucket,
        Key=storage_key,
        Body=payload,
        ContentType=content_type,
    )


def create_download_url(storage_key: str, expires_in: int = 3600, attachment_filename: str | None = None) -> str:
    params = {"Bucket": settings.s3_bucket, "Key": storage_key}
    if attachment_filename:
        # Browsers honour this response header across origins, unlike a bare HTML `download`
        # attribute on a MinIO presigned URL.
        params["ResponseContentDisposition"] = f'attachment; filename="{attachment_filename}"'
    return _client(settings.s3_public_endpoint_url).generate_presigned_url(
        ClientMethod="get_object",
        Params=params,
        ExpiresIn=expires_in,
        HttpMethod="GET",
    )


def delete_object(storage_key: str) -> None:
    _client(settings.s3_endpoint_url).delete_object(Bucket=settings.s3_bucket, Key=storage_key)


RAW_ARCHIVE_LIFECYCLE_RULE_ID = "mam-raw-4k-deep-archive"


def configure_raw_archive_lifecycle() -> None:
    """Install/update one tag-scoped rule without replacing unrelated bucket lifecycle rules."""
    if not settings.mam_s3_lifecycle_enabled:
        return
    client = _client(settings.s3_endpoint_url)
    try:
        existing = client.get_bucket_lifecycle_configuration(Bucket=settings.s3_bucket).get("Rules", [])
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") not in {"NoSuchLifecycleConfiguration", "NoSuchBucket"}:
            raise
        existing = []
    rule = {
        "ID": RAW_ARCHIVE_LIFECYCLE_RULE_ID,
        "Status": "Enabled",
        # The scheduler adds this tag only after completed+inactive policy verification.
        "Filter": {"And": {"Tags": [{"Key": "mam-tier", "Value": "cold"}, {"Key": "asset-role", "Value": "raw"}]}},
        "Transitions": [{"Days": 1, "StorageClass": "DEEP_ARCHIVE"}],
    }
    client.put_bucket_lifecycle_configuration(
        Bucket=settings.s3_bucket,
        LifecycleConfiguration={"Rules": [item for item in existing if item.get("ID") != RAW_ARCHIVE_LIFECYCLE_RULE_ID] + [rule]},
    )


def tag_for_deep_archive(storage_key: str) -> None:
    if not settings.mam_s3_lifecycle_enabled:
        return
    client = _client(settings.s3_endpoint_url)
    try:
        tag_set = client.get_object_tagging(Bucket=settings.s3_bucket, Key=storage_key).get("TagSet", [])
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "NoSuchTagSet":
            raise
        tag_set = []
    tags = {str(item["Key"]): str(item["Value"]) for item in tag_set}
    tags.update({"mam-tier": "cold", "asset-role": "raw"})
    client.put_object_tagging(
        Bucket=settings.s3_bucket, Key=storage_key,
        Tagging={"TagSet": [{"Key": key, "Value": value} for key, value in tags.items()]},
    )


def object_archive_info(storage_key: str) -> dict[str, object]:
    """Return a portable view of S3's storage class and Glacier restore header."""
    response = _client(settings.s3_endpoint_url).head_object(Bucket=settings.s3_bucket, Key=storage_key)
    return {
        "storage_class": response.get("StorageClass", "STANDARD"),
        "restore": response.get("Restore"),
        "content_length": response.get("ContentLength"),
    }


def initiate_deep_archive_restore(storage_key: str) -> None:
    if not settings.mam_s3_lifecycle_enabled:
        raise RuntimeError("Cold-storage restore is unavailable because MAM_S3_LIFECYCLE_ENABLED is false")
    _client(settings.s3_endpoint_url).restore_object(
        Bucket=settings.s3_bucket,
        Key=storage_key,
        RestoreRequest={"Days": settings.mam_restore_days, "GlacierJobParameters": {"Tier": settings.mam_restore_tier}},
    )
