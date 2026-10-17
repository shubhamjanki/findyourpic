from __future__ import annotations

import base64
from io import BytesIO
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from PIL import Image
import pytest

try:
    from backend import app as backend_app
    from backend.storage import S3Storage, StorageError
except ImportError:
    from apps.backend import app as backend_app
    from apps.backend.storage import S3Storage, StorageError


class FakeS3Client:
    def __init__(self) -> None:
        self.upload: dict[str, Any] | None = None
        self.head: dict[str, Any] | None = None
        self.objects = [
            "photos/first.jpg",
            "photos/notes.txt",
            "photos/second.png",
            "photos/third.webp",
        ]
        self.list_requests: list[dict[str, Any]] = []
        self.image_data = _image_bytes()

    def put_object(self, **kwargs: Any) -> dict[str, str]:
        self.upload = kwargs
        return {"ETag": '"test-etag"'}

    def head_object(self, **kwargs: Any) -> dict[str, int]:
        self.head = kwargs
        return {"ContentLength": len(self.upload["Body"])}

    def list_objects_v2(self, **kwargs: Any) -> dict[str, Any]:
        self.list_requests.append(kwargs)
        keys = [
            key for key in self.objects
            if key.startswith(kwargs["Prefix"])
            and (not kwargs.get("StartAfter") or key > kwargs["StartAfter"])
        ]
        page = keys[:kwargs["MaxKeys"]]
        return {
            "Contents": [{"Key": key} for key in page],
            "IsTruncated": len(keys) > len(page),
        }

    def get_object(self, **kwargs: Any) -> dict[str, Any]:
        return {"Body": BytesIO(self.image_data)}


def _image_bytes() -> bytes:
    output = BytesIO()
    Image.new("RGB", (3, 2), (10, 20, 30)).save(output, format="JPEG")
    return output.getvalue()


def test_s3_storage_uploads_private_encrypted_object() -> None:
    client = FakeS3Client()
    storage = S3Storage("private-bucket", "us-east-1", client=client)

    result = storage.upload_check(_image_bytes(), "sample.jpg", "image/jpeg")

    assert result == {
        "bucket": "private-bucket",
        "key": client.upload["Key"],
        "etag": "test-etag",
        "size": len(_image_bytes()),
        "content_type": "image/jpeg",
    }
    assert client.upload["Key"].startswith("smoke-tests/")
    assert client.upload["ServerSideEncryption"] == "AES256"
    assert "ACL" not in client.upload
    assert client.head == {"Bucket": "private-bucket", "Key": result["key"]}


def test_s3_storage_requires_bucket_and_region(monkeypatch: Any) -> None:
    monkeypatch.delenv("S3_BUCKET", raising=False)
    monkeypatch.delenv("AWS_REGION", raising=False)
    monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)

    try:
        S3Storage()
    except StorageError as exc:
        assert "S3_BUCKET" in str(exc)
    else:
        raise AssertionError("Expected missing bucket configuration to fail.")


def test_s3_storage_lists_supported_images_and_paginates_by_key() -> None:
    client = FakeS3Client()
    storage = S3Storage("private-bucket", "us-east-1", prefix="photos/", client=client)

    first = storage.list_images(page_size=2)
    second = storage.list_images(page_size=2, start_after=first["next_token"])

    assert [photo["path"] for photo in first["photos"]] == [
        "photos/first.jpg",
        "photos/second.png",
    ]
    assert first["next_token"] == "photos/second.png"
    assert [photo["path"] for photo in second["photos"]] == ["photos/third.webp"]
    assert second["next_token"] is None
    assert client.list_requests[0]["Prefix"] == "photos/"
    assert client.list_requests[1]["StartAfter"] == "photos/second.png"


def test_s3_storage_reads_only_supported_images_in_configured_prefix() -> None:
    client = FakeS3Client()
    storage = S3Storage("private-bucket", "us-east-1", prefix="photos/", client=client)

    assert storage.read_image("photos/first.jpg") == client.image_data
    with pytest.raises(ValueError, match="configured prefix"):
        storage.read_image("other/first.jpg")
    with pytest.raises(ValueError, match="supported image"):
        storage.read_image("photos/readme.txt")


def test_fastapi_s3_check_upload_sends_valid_image_to_s3(
    monkeypatch: Any, tmp_path: Path
) -> None:
    monkeypatch.setenv("S3_CHECK_UPLOAD_ENABLED", "true")
    client = FakeS3Client()
    storage = S3Storage("private-bucket", "us-east-1", client=client)
    monkeypatch.setattr(backend_app, "create_s3_storage", lambda: storage)
    image = _image_bytes()

    response = TestClient(backend_app.create_app(photo_root=tmp_path)).post(
        "/api/storage/s3/check-upload",
        json={"name": "sample.jpg", "data": base64.b64encode(image).decode("ascii")},
    )

    assert response.status_code == 200
    assert response.json()["bucket"] == "private-bucket"
    assert response.json()["size"] == len(image)
    assert client.upload["Body"] == image


def test_fastapi_s3_browse_lists_and_serves_private_images(
    monkeypatch: Any, tmp_path: Path
) -> None:
    monkeypatch.setenv("S3_BROWSE_ENABLED", "true")
    client = FakeS3Client()
    storage = S3Storage("private-bucket", "us-east-1", prefix="photos/", client=client)
    monkeypatch.setattr(backend_app, "create_s3_storage", lambda: storage)
    api = TestClient(backend_app.create_app(photo_root=tmp_path))

    listing = api.get("/api/s3/photos", params={"page_size": 2})
    image = api.get("/api/s3/image", params={"key": "photos/first.jpg"})

    assert listing.status_code == 200
    assert [photo["path"] for photo in listing.json()["photos"]] == [
        "photos/first.jpg",
        "photos/second.png",
    ]
    assert listing.json()["next_token"] == "photos/second.png"
    assert image.status_code == 200
    assert image.headers["content-type"] == "image/jpeg"
    assert image.content == client.image_data


def test_fastapi_s3_browse_is_disabled_by_default(
    monkeypatch: Any, tmp_path: Path
) -> None:
    monkeypatch.delenv("S3_BROWSE_ENABLED", raising=False)
    response = TestClient(backend_app.create_app(photo_root=tmp_path)).get("/api/s3/photos")

    assert response.status_code == 404
    assert response.json()["error"] == "S3 image browsing is disabled."


def test_fastapi_s3_check_upload_rejects_invalid_image(
    monkeypatch: Any, tmp_path: Path
) -> None:
    monkeypatch.setenv("S3_CHECK_UPLOAD_ENABLED", "true")

    def unexpected_storage() -> S3Storage:
        raise AssertionError("S3 should not be contacted for invalid image data.")

    monkeypatch.setattr(backend_app, "create_s3_storage", unexpected_storage)
    response = TestClient(backend_app.create_app(photo_root=tmp_path)).post(
        "/api/storage/s3/check-upload",
        json={"name": "sample.jpg", "data": base64.b64encode(b"not an image").decode("ascii")},
    )

    assert response.status_code == 400
    assert "valid image" in response.json()["error"]


def test_fastapi_s3_check_upload_is_disabled_by_default(
    monkeypatch: Any, tmp_path: Path
) -> None:
    monkeypatch.delenv("S3_CHECK_UPLOAD_ENABLED", raising=False)
    response = TestClient(backend_app.create_app(photo_root=tmp_path)).post(
        "/api/storage/s3/check-upload",
        json={"name": "sample.jpg", "data": base64.b64encode(_image_bytes()).decode("ascii")},
    )

    assert response.status_code == 404
    assert response.json()["error"] == "S3 smoke uploads are disabled."
