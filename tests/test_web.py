from __future__ import annotations

import sqlite3
import zipfile
from io import BytesIO
from pathlib import Path

from PIL import Image
from fastapi.testclient import TestClient

from apps.backend.app import create_app
from apps.backend.service import ensure_custom_tables
from face_search.index import index_directory
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
