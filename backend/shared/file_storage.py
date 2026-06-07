"""
File storage — saves to local filesystem under LOCAL_UPLOADS_DIR.
Falls back to S3/MinIO when S3_ENDPOINT_URL is set (production path).
"""
import asyncio
import os
import uuid
from pathlib import Path

LOCAL_UPLOADS_DIR = os.getenv("LOCAL_UPLOADS_DIR", "./uploads")
S3_ENDPOINT_URL = os.getenv("S3_ENDPOINT_URL", "")  # empty = local mode
S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME", "visually-uploads")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")

_USE_LOCAL = not S3_ENDPOINT_URL and not os.getenv("AWS_S3_FORCE_REMOTE", "")


# ─── Local filesystem ─────────────────────────────────────────────────────────

def _local_upload(file_bytes: bytes, filename: str, project_id: str) -> dict:
    ext = Path(filename).suffix.lower() or ".png"
    file_id = str(uuid.uuid4())
    rel_key = f"screenshots/{project_id}/{file_id}{ext}"
    dest = Path(LOCAL_UPLOADS_DIR) / rel_key
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(file_bytes)
    return {"s3_key": rel_key, "size_bytes": len(file_bytes), "local_path": str(dest)}


def _local_download(s3_key: str) -> bytes:
    path = Path(LOCAL_UPLOADS_DIR) / s3_key
    if not path.exists():
        raise FileNotFoundError(f"Uploaded file not found: {path}")
    return path.read_bytes()


# ─── S3 / MinIO ───────────────────────────────────────────────────────────────

def _get_s3_client():
    import boto3
    kwargs = {"service_name": "s3", "region_name": AWS_REGION}
    if S3_ENDPOINT_URL:
        kwargs["endpoint_url"] = S3_ENDPOINT_URL
        kwargs["aws_access_key_id"] = os.getenv("MINIO_ROOT_USER", "minioadmin")
        kwargs["aws_secret_access_key"] = os.getenv("MINIO_ROOT_PASSWORD", "minioadmin")
    else:
        if os.getenv("AWS_ACCESS_KEY_ID"):
            kwargs["aws_access_key_id"] = os.getenv("AWS_ACCESS_KEY_ID")
            kwargs["aws_secret_access_key"] = os.getenv("AWS_SECRET_ACCESS_KEY")
    return boto3.client(**kwargs)


def _s3_upload(file_bytes: bytes, filename: str, mime_type: str, project_id: str) -> dict:
    ext = Path(filename).suffix.lower() or ".png"
    s3_key = f"screenshots/{project_id}/{uuid.uuid4()}{ext}"
    client = _get_s3_client()
    try:
        client.head_bucket(Bucket=S3_BUCKET_NAME)
    except Exception:
        try:
            client.create_bucket(Bucket=S3_BUCKET_NAME)
        except Exception:
            pass
    client.put_object(Bucket=S3_BUCKET_NAME, Key=s3_key, Body=file_bytes, ContentType=mime_type)
    return {"s3_key": s3_key, "size_bytes": len(file_bytes)}


def _s3_download(s3_key: str) -> bytes:
    client = _get_s3_client()
    response = client.get_object(Bucket=S3_BUCKET_NAME, Key=s3_key)
    return response["Body"].read()


# ─── Public async interface ───────────────────────────────────────────────────

async def upload_file(
    file_bytes: bytes,
    filename: str,
    mime_type: str,
    project_id: str,
) -> dict:
    """Upload file. Returns {"s3_key": str, "size_bytes": int}."""
    if _USE_LOCAL:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _local_upload, file_bytes, filename, project_id)
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _s3_upload, file_bytes, filename, mime_type, project_id)


async def download_file(s3_key: str) -> bytes:
    """Download file by its storage key. Works for both local and S3."""
    if _USE_LOCAL:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _local_download, s3_key)
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _s3_download, s3_key)
