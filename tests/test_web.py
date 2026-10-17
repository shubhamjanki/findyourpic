from __future__ import annotations

import sqlite3
import threading
import time
import zipfile
from io import BytesIO
from pathlib import Path

from PIL import Image
from fastapi.testclient import TestClient

try:
    from backend.app import create_app
    from backend.service import ensure_custom_tables
except ImportError:
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
