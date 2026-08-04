"""DO Spaces integration: presigned PUT URLs for borrower uploads, presigned GET
for broker download. boto3 is the standard S3 client; we just point it at the
DO Spaces endpoint.

This is intentionally small. The borrower portal calls `presign_upload`,
gets a URL, uploads directly to Spaces from the browser. The broker view
calls `presign_download` to view a file. No proxying through our app.
"""
import os, uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from .config import config


def get_s3_client():
    return boto3.client(
        "s3",
        region_name=config.SPACES_REGION,
        endpoint_url=config.SPACES_ENDPOINT,
        aws_access_key_id=config.SPACES_ACCESS_KEY,
        aws_secret_access_key=config.SPACES_SECRET_KEY,
        config=Config(signature_version="s3v4"),
    )


def _key_for(deal_public_id: str, purpose: str, filename: str) -> str:
    """Build a deterministic, clean key under the deal's prefix."""
    safe = filename.replace("/", "_").replace("\\", "_")
    deal_dir = f"deals/{deal_public_id}"
    return f"{deal_dir}/{purpose}/{uuid.uuid4().hex[:8]}-{safe}"


def presign_upload(deal_public_id: str, purpose: str, filename: str,
                   content_type: str = "application/octet-stream",
                   expires_in: int = 900,
                   max_size_mb: int = 50) -> dict:
    """Return {url, key, expires_at, max_size} for a presigned PUT.

    The browser uploads directly to Spaces using the returned URL.
    `expires_in` is the URL lifetime in seconds (default 15 min).
    `max_size_mb` is what we tell the browser (we set Content-Length on the
    PUT policy).
    """
    if not config.SPACES_ACCESS_KEY or not config.SPACES_SECRET_KEY:
        raise RuntimeError("Spaces credentials not configured")

    s3 = get_s3_client()
    key = _key_for(deal_public_id, purpose, filename)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    url = s3.generate_presigned_url(
        "put_object",
        Params={
            "Bucket": config.SPACES_BUCKET,
            "Key": key,
            "ContentType": content_type,
        },
        ExpiresIn=expires_in,
    )
    return {
        "url": url,
        "key": key,
        "expires_at": expires_at.isoformat(),
        "max_size_bytes": max_size_mb * 1024 * 1024,
        "method": "PUT",
        "headers": {"Content-Type": content_type},
    }


def presign_download(spaces_key: str, expires_in: int = 3600) -> str:
    """Return a presigned GET URL the broker can use to view/download a file."""
    s3 = get_s3_client()
    return s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": config.SPACES_BUCKET, "Key": spaces_key},
        ExpiresIn=expires_in,
    )


def head_object(spaces_key: str) -> Optional[dict]:
    """Return metadata for an object, or None if it doesn't exist."""
    s3 = get_s3_client()
    try:
        r = s3.head_object(Bucket=config.SPACES_BUCKET, Key=spaces_key)
        return {
            "size_bytes": r.get("ContentLength"),
            "content_type": r.get("ContentType"),
            "last_modified": r.get("LastModified").isoformat() if r.get("LastModified") else None,
        }
    except ClientError as e:
        if e.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
            return None
        raise


def delete_object(spaces_key: str) -> bool:
    s3 = get_s3_client()
    try:
        s3.delete_object(Bucket=config.SPACES_BUCKET, Key=spaces_key)
        return True
    except ClientError:
        return False


def public_url(spaces_key: str) -> str:
    """The CDN-served public URL. Use when the file is meant to be shared."""
    return f"{config.SPACES_CDN_ENDPOINT}/{spaces_key}"