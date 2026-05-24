"""Object storage (S3 / SeaweedFS) for PDFs.

boto3 is synchronous; callers wrap these in run_in_threadpool. We talk only to the S3
API so the backing store (SeaweedFS now, Garage / Ceph-RGW later) is swappable.
"""
from __future__ import annotations

import boto3
from botocore.client import Config

from .config import get_settings

settings = get_settings()


def _client(endpoint: str | None = None):
    return boto3.client(
        "s3",
        endpoint_url=endpoint or settings.s3_endpoint_url,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name=settings.s3_region,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


def ensure_bucket() -> None:
    client = _client()
    existing = {b["Name"] for b in client.list_buckets().get("Buckets", [])}
    if settings.s3_bucket not in existing:
        client.create_bucket(Bucket=settings.s3_bucket)


def upload_bytes(key: str, data: bytes, content_type: str) -> None:
    _client().put_object(
        Bucket=settings.s3_bucket, Key=key, Body=data, ContentType=content_type
    )


def download_bytes(key: str) -> bytes:
    return _client().get_object(Bucket=settings.s3_bucket, Key=key)["Body"].read()


def presigned_get_url(key: str, download_name: str | None = None) -> str:
    """Presigned GET URL, signed with the *public* endpoint the browser can reach."""
    params = {"Bucket": settings.s3_bucket, "Key": key}
    if download_name:
        params["ResponseContentDisposition"] = f'attachment; filename="{download_name}"'
    return _client(settings.s3_public_endpoint_url).generate_presigned_url(
        "get_object", Params=params, ExpiresIn=settings.presign_expiry_seconds
    )
