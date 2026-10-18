from __future__ import annotations

import base64
import sqlite3
import threading
import time
import zipfile
from io import BytesIO
from pathlib import Path

from PIL import Image
from fastapi.testclient import TestClient

try:
    from backend import app as backend_app
    from backend.app import create_app
    from backend.service import ensure_custom_tables
except ImportError:
    from apps.backend import app as backend_app
    from apps.backend.app import create_app
    from apps.backend.service import ensure_custom_tables
from face_search.index import IndexReport, index_directory
from test_support import FakeBackend


def _write_image(path: Path, color: tuple[int, int, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (20, 12), color).save(path)


def _client(database: Path, collection: Path) -> TestClient:
    return TestClient(create_app(database=database, photo_root=collection))


def test_people_search_matches_custom_names_case_insensitively(tmp_path: Path) -> None:
    collection = tmp_path / "photos"
    _write_image(collection / "first.jpg", (255, 0, 0))
    database = tmp_path / "index.sqlite3"
    index_directory(collection, database, backend=FakeBackend(), show_progress=False)

    with sqlite3.connect(database) as connection:
        ensure_custom_tables(connection)
        path, face_index = connection.execute(
            "SELECT path, face_index FROM faces LIMIT 1"
        ).fetchone()
        connection.execute(
            "INSERT INTO person_labels (person_key, name) VALUES (?, ?)",
            (f"{path}#{face_index}", "Aarav"),
        )

    client = _client(database, collection)
    result = client.get("/api/people", params={"q": "AARAV", "page": 1, "page_size": 60}).json()

    assert result["total"] == 1
    assert result["people"][0]["name"] == "Aarav"


def test_person_thumbnail_upload_is_saved_and_served(
    monkeypatch, tmp_path: Path
) -> None:
    collection = tmp_path / "photos"
    _write_image(collection / "first.jpg", (255, 0, 0))
    database = tmp_path / "index.sqlite3"
    index_directory(collection, database, backend=FakeBackend(), show_progress=False)
    thumbnail_dir = tmp_path / "face-thumbnails"
    monkeypatch.setattr(backend_app.service, "FACE_THUMB_DIR", thumbnail_dir)
    client = _client(database, collection)
    person = client.get("/api/people").json()["people"][0]

    upload = BytesIO()
    Image.new("RGB", (32, 24), (0, 128, 255)).save(upload, format="PNG")
    response = client.post(
        "/api/person-thumbnail",
        json={
            "key": person["key"],
            "data": base64.b64encode(upload.getvalue()).decode("ascii"),
        },
    )

    assert response.status_code == 200
    assert response.json()["key"] == person["key"]
    assert response.json()["thumbnail_version"]

    updated_person = client.get("/api/people").json()["people"][0]
    assert updated_person["custom_thumbnail_version"] == response.json()["thumbnail_version"]
    image = client.get("/api/person-thumbnail", params={"key": person["key"]})
    assert image.status_code == 200
    assert image.headers["content-type"] == "image/webp"
    assert Image.open(BytesIO(image.content)).size == (32, 24)


def test_detect_reuses_face_backend_for_camera_search(
    monkeypatch, tmp_path: Path
) -> None:
    collection = tmp_path / "photos"
    collection.mkdir()
    client = _client(tmp_path / "index.sqlite3", collection)
    backend = FakeBackend()
    created: list[str] = []

    def create_backend(name: str) -> FakeBackend:
        created.append(name)
        return backend

    monkeypatch.setattr(backend_app, "create_backend", create_backend)
    upload = BytesIO()
    Image.new("RGB", (20, 12), (200, 100, 50)).save(upload, format="JPEG")
    payload = {
        "name": "camera.jpg",
        "data": base64.b64encode(upload.getvalue()).decode("ascii"),
        "backend": "insightface",
    }

    first = client.post("/api/detect", json=payload)
    second = client.post("/api/detect", json=payload)

    assert first.status_code == second.status_code == 200
    assert created == ["insightface"]


def test_download_zip_contains_selected_photos_and_preserves_folders(tmp_path: Path) -> None:
    collection = tmp_path / "photos"
    _write_image(collection / "event" / "first.jpg", (255, 0, 0))
    _write_image(collection / "second.jpg", (0, 255, 0))
    database = tmp_path / "index.sqlite3"
    index_directory(collection, database, backend=FakeBackend(), show_progress=False)

    client = _client(database, collection)
    response = client.post("/api/download-zip", json={"paths": ["event/first.jpg", "second.jpg"]})
    assert response.status_code == 200

    with zipfile.ZipFile(BytesIO(response.content)) as archive:
        assert archive.namelist() == ["event/first.jpg", "second.jpg"]
        assert archive.read("event/first.jpg") == (collection / "event" / "first.jpg").read_bytes()
        assert archive.read("second.jpg") == (collection / "second.jpg").read_bytes()


def test_download_zip_rejects_paths_outside_indexed_collection(tmp_path: Path) -> None:
    collection = tmp_path / "photos"
    _write_image(collection / "first.jpg", (255, 0, 0))
    database = tmp_path / "index.sqlite3"
    index_directory(collection, database, backend=FakeBackend(), show_progress=False)

    client = _client(database, collection)
    response = client.post("/api/download-zip", json={"paths": ["../outside.jpg"]})
    assert response.status_code == 403
    assert "outside" in response.json()["error"].lower()


def test_health_and_config_use_env_paths_not_query_db(tmp_path: Path) -> None:
    collection = tmp_path / "photos"
    _write_image(collection / "first.jpg", (255, 0, 0))
    database = tmp_path / "index.sqlite3"
    index_directory(collection, database, backend=FakeBackend(), show_progress=False)
    client = _client(database, collection)
    assert client.get("/api/health").json() == {"status": "ok"}
    config = client.get("/api/config", params={"db": "/tmp/evil.sqlite3"}).json()
    assert config["database"] == str(database)
    assert Path(config["collection"]) == collection.resolve()
    assert config["stats"]["files"] == 1


def test_index_job_reports_progress_and_completion(
    tmp_path: Path, monkeypatch
) -> None:
    collection = tmp_path / "photos"
    collection.mkdir()
    database = tmp_path / "index.sqlite3"
    progress_sent = threading.Event()
    continue_index = threading.Event()

    def fake_index(
        _collection: Path,
        _database: Path,
        _backend: str,
        *,
        workers: int,
        show_progress: bool,
        progress_callback=None,
    ) -> IndexReport:
        assert workers == 1
        assert show_progress is False
        progress_callback("checking", 1, 2)
        progress_sent.set()
        assert continue_index.wait(timeout=5)
        progress_callback("indexing", 1, 1)
        return IndexReport(scanned=2, indexed=1, skipped=1, face_count=1)

    app_module = create_app.__module__
    monkeypatch.setattr(f"{app_module}.index_directory", fake_index)
    monkeypatch.setattr(f"{app_module}.index_stats", lambda _database: {"files": 1})
    client = _client(database, collection)
    try:
        start = client.post("/api/index/start", json={"backend": "insightface", "workers": 1})
        assert start.status_code == 202
        job_id = start.json()["job_id"]
        assert progress_sent.wait(timeout=5)

        progress = client.get(f"/api/index/status/{job_id}").json()
        assert (progress["status"], progress["phase"], progress["completed"], progress["total"]) == (
            "running", "checking", 1, 2
        )

        continue_index.set()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            result = client.get(f"/api/index/status/{job_id}").json()
            if result["status"] == "completed":
                break
            time.sleep(0.01)
        assert result["status"] == "completed"
        assert result["phase"] == "indexing"
        assert result["report"]["indexed"] == 1
    finally:
        continue_index.set()


def test_thumbnail_job_generates_photo_and_face_thumbnails(
    monkeypatch, tmp_path: Path
) -> None:
    collection = tmp_path / "photos"
    image_path = collection / "first.jpg"
    _write_image(image_path, (255, 0, 0))
    database = tmp_path / "index.sqlite3"
    thumbnails = tmp_path / "thumbnails"
    face_thumbnails = tmp_path / "face-thumbnails"

    class FakeStorage:
        def list_all_keys(self) -> list[dict[str, str]]:
            return [{"key": "first.jpg"}]

        def read_image(self, _key: str) -> bytes:
            return image_path.read_bytes()

    monkeypatch.setattr(backend_app, "create_s3_storage", FakeStorage)
    monkeypatch.setattr(
        backend_app.service,
        "list_people",
        lambda *_args, **_kwargs: {
            "people": [{
                "preview": {
                    "path": "first.jpg",
                    "bbox": {"x1": 2, "y1": 2, "x2": 10, "y2": 10},
                },
            }],
            "total": 1,
        },
    )
    monkeypatch.setattr(backend_app.service, "THUMB_DIR", thumbnails)
    monkeypatch.setattr(backend_app.service, "FACE_THUMB_DIR", face_thumbnails)
    monkeypatch.setattr(backend_app.service, "ALT_THUMB_DIR", tmp_path / "no-alt-thumbnails")
    monkeypatch.setattr(
        backend_app.service, "ALT_FACE_THUMB_DIR", tmp_path / "no-alt-face-thumbnails"
    )

    client = TestClient(create_app(database=database, photo_root=collection))
    start = client.post("/api/thumbnails/start", json={})
    assert start.status_code == 202
    job_id = start.json()["job_id"]

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        result = client.get(f"/api/thumbnails/status/{job_id}").json()
        if result["status"] in ("completed", "failed"):
            break
        time.sleep(0.01)

    assert result == {
        "job_id": job_id,
        "status": "completed",
        "completed": 2,
        "total": 2,
        "ok": 2,
        "errors": 0,
    }
    assert len(list(thumbnails.glob("*.webp"))) == 1
    assert len(list(face_thumbnails.glob("*.webp"))) == 1
