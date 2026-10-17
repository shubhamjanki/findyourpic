"""Photo object access for local collections and S3 upload checks."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from uuid import uuid4

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from face_search.utils import SUPPORTED_EXTENSIONS


class StorageError(RuntimeError):
    """Raised when a storage backend is missing configuration or cannot serve an object."""


class LocalStorage:
    """Read originals from a bind-mounted photo folder."""

    def __init__(self, root: Path) -> None:
        self.root = root.expanduser().resolve()

    def resolve(self, requested: str) -> Path:
        if not requested:
            raise StorageError("Missing image path.")
        # Detect Windows-style absolute paths (e.g. C:\Users\...) that are stored in the
        # index but may be served from a Linux container.  pathlib on Linux does not
        # recognise them as absolute, so we parse them with PureWindowsPath first to
        # extract the plain filename and then fall back to resolving by name only.
        from pathlib import PureWindowsPath
        raw = requested.strip()
        win_path = PureWindowsPath(raw)
        is_windows_absolute = win_path.drive != ""  # e.g. "C:"
        if is_windows_absolute:
            # Fall back immediately to basename lookup within our root.
            image_path = (self.root / win_path.name).resolve()
            try:
                image_path.relative_to(self.root)
            except ValueError as exc:
                raise PermissionError("Image is outside the indexed collection.") from exc
            if image_path.suffix.lower() not in SUPPORTED_EXTENSIONS or not image_path.is_file():
                raise FileNotFoundError("Indexed image is missing or unsupported.")
            return image_path
        requested_path = Path(raw).expanduser()
        image_path = (
            requested_path.resolve()
            if requested_path.is_absolute()
            else (self.root / requested_path).resolve()
        )
        try:
            image_path.relative_to(self.root)
        except ValueError as exc:
            fallback = (self.root / requested_path.name).resolve()
            if fallback.is_file() and fallback.suffix.lower() in SUPPORTED_EXTENSIONS:
                return fallback
            raise PermissionError("Image is outside the indexed collection.") from exc
        if image_path.suffix.lower() not in SUPPORTED_EXTENSIONS or not image_path.is_file():
            fallback = (self.root / Path(raw).name).resolve()
            if fallback.is_file() and fallback.suffix.lower() in SUPPORTED_EXTENSIONS:
                return fallback
            raise FileNotFoundError("Indexed image is missing or unsupported.")
        return image_path

    def relative_name(self, image_path: Path) -> str:
        return image_path.relative_to(self.root).as_posix()


class S3Storage:
    """Read private images and upload smoke-test objects in an S3 bucket."""

    def __init__(
        self,
        bucket: str | None = None,
        region: str | None = None,
        *,
        prefix: str | None = None,
        client: Any | None = None,
    ) -> None:
        self.bucket = (bucket or os.environ.get("S3_BUCKET", "")).strip()
        self.prefix = (prefix if prefix is not None else os.environ.get("S3_PREFIX", "")).strip()
        self.region = (
            region
            or os.environ.get("AWS_REGION")
            or os.environ.get("AWS_DEFAULT_REGION")
            or ""
        ).strip()
        if not self.bucket:
            raise StorageError("Set S3_BUCKET to the name of your private S3 bucket.")
        if not self.region:
            raise StorageError("Set AWS_REGION to your S3 bucket's region.")
        from botocore.config import Config
        boto_config = Config(connect_timeout=5, read_timeout=15, retries={"max_attempts": 2})
        aws_key = os.environ.get("AWS_ACCESS_KEY_ID", "").strip() or None
        aws_secret = os.environ.get("AWS_SECRET_ACCESS_KEY", "").strip() or None
        self.client = (
            client
            if client is not None
            else boto3.client(
                "s3",
                region_name=self.region,
                aws_access_key_id=aws_key,
                aws_secret_access_key=aws_secret,
                config=boto_config,
            )
        )

    def list_images(
        self, *, page_size: int = 24, start_after: str | None = None
    ) -> dict[str, Any]:
        if not 1 <= page_size <= 60:
            raise ValueError("page_size must be between 1 and 60.")
        if start_after and self.prefix and not start_after.startswith(self.prefix):
            raise ValueError("The pagination key is outside the configured S3 prefix.")

        photos: list[dict[str, Any]] = []
        marker = start_after
        try:
            while True:
                request: dict[str, Any] = {
                    "Bucket": self.bucket,
                    "Prefix": self.prefix,
                    "MaxKeys": 1000,
                }
                if marker:
                    request["StartAfter"] = marker
                response = self.client.list_objects_v2(**request)
                objects = response.get("Contents", [])
                for item in objects:
                    key = item.get("Key", "")
                    if not key:
                        continue
                    marker = key
                    if Path(key).suffix.lower() not in SUPPORTED_EXTENSIONS:
                        continue
                    photos.append({"path": key, "source": "s3", "faces": 0})
                    if len(photos) == page_size:
                        return {"photos": photos, "next_token": key}
                if not response.get("IsTruncated"):
                    break
                if not objects:
                    raise StorageError("S3 returned a truncated image listing without a next key.")
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "Unknown")
            raise StorageError(
                f"S3 image listing failed ({code}). Check s3:ListBucket access for this bucket."
            ) from exc
        except BotoCoreError as exc:
            raise StorageError(
                "Could not connect to S3. Check the network and AWS credential configuration."
            ) from exc
        return {"photos": photos, "next_token": None}

    def list_all_keys(self) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        paginator = self.client.get_paginator("list_objects_v2")
        try:
            for page in paginator.paginate(Bucket=self.bucket, Prefix=self.prefix):
                for item in page.get("Contents", []):
                    key = item.get("Key", "")
                    if not key or Path(key).suffix.lower() not in SUPPORTED_EXTENSIONS:
                        continue
                    mtime_dt = item.get("LastModified")
                    mtime_ns = int(mtime_dt.timestamp() * 1e9) if mtime_dt else 0
                    results.append({
                        "key": key,
                        "size": item.get("Size", 0),
                        "etag": item.get("ETag", "").strip('"'),
                        "mtime_ns": mtime_ns,
                    })
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "Unknown")
            raise StorageError(f"S3 listing failed ({code}). Check s3:ListBucket permissions.") from exc
        except BotoCoreError as exc:
            raise StorageError("Could not connect to S3. Check network and AWS credentials.") from exc
        return results

    def read_image(self, key: str) -> bytes:
        if (
            not key
            or Path(key).suffix.lower() not in SUPPORTED_EXTENSIONS
            or (self.prefix and not key.startswith(self.prefix))
        ):
            raise ValueError("The requested S3 key is not a supported image in the configured prefix.")
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=key)
            body = response["Body"]
            try:
                return body.read()
            finally:
                body.close()
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "Unknown")
            if code in {"NoSuchKey", "404", "NotFound"}:
                raise FileNotFoundError("S3 image was not found.") from exc
            raise StorageError(
                f"S3 image retrieval failed ({code}). Check s3:GetObject access for this key."
            ) from exc
        except BotoCoreError as exc:
            raise StorageError(
                "Could not connect to S3. Check the network and AWS credential configuration."
            ) from exc

    def upload_check(self, contents: bytes, filename: str, content_type: str) -> dict[str, Any]:
        suffix = Path(filename).suffix.lower()
        if suffix not in SUPPORTED_EXTENSIONS:
            raise ValueError("Supported image types: JPG, PNG, WEBP, and BMP.")
        if not contents:
            raise ValueError("The uploaded image is empty.")

        key = f"smoke-tests/{uuid4().hex}{suffix}"
        try:
            result = self.client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=contents,
                ContentType=content_type,
                ServerSideEncryption="AES256",
            )
            verified = self.client.head_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "Unknown")
            raise StorageError(
                f"S3 upload or verification failed ({code}). Check the bucket region "
                "and the IAM permissions for this bucket."
            ) from exc
        except BotoCoreError as exc:
            raise StorageError(
                "Could not connect to S3. Check the network and AWS credential configuration."
            ) from exc

        if verified.get("ContentLength") != len(contents):
            raise StorageError("S3 upload verification failed: the object size does not match.")
        return {
            "bucket": self.bucket,
            "key": key,
            "etag": result.get("ETag", "").strip('"'),
            "size": len(contents),
            "content_type": content_type,
        }


def create_s3_storage() -> S3Storage:
    return S3Storage()


class R2Storage:
    """Placeholder for Cloudflare R2 originals and thumbnails."""

    def __init__(self) -> None:
        account = os.environ.get("R2_ACCOUNT_ID", "")
        bucket = os.environ.get("R2_BUCKET", "")
        if not account or not bucket:
            raise StorageError(
                "Cloudflare R2 is not configured. Use STORAGE_BACKEND=local for Docker Compose, "
                "or set R2_ACCOUNT_ID and R2_BUCKET later."
            )
        raise StorageError("Cloudflare R2 storage is a stub in this release.")


class SupabaseStorage:
    """Placeholder for Supabase Storage originals and thumbnails."""

    def __init__(self) -> None:
        url = os.environ.get("SUPABASE_URL", "")
        if not url:
            raise StorageError(
                "Supabase storage is not configured. Use STORAGE_BACKEND=local for Docker Compose, "
                "or set SUPABASE_URL later."
            )
        raise StorageError("Supabase storage is a stub in this release.")


def create_storage(root: Path, backend: str = "local") -> LocalStorage:
    if backend == "r2":
        return R2Storage()
    if backend == "supabase":
        return SupabaseStorage()
    if backend != "local":
        raise StorageError(f"Unknown storage backend: {backend}")
    return LocalStorage(root)
