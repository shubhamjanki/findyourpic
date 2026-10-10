"""FastAPI process for Find Your Pic. Bind through Docker to localhost on the host."""

from __future__ import annotations

import base64
import binascii
import logging
import mimetypes
import shutil
import sqlite3
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from apps.backend.settings import Settings
from apps.backend.storage import StorageError
from apps.backend import service
from face_search.backends import create_backend
from face_search.index import clear_index, index_directory, index_stats
from face_search.search import (
    NoFaceError,
    average_embeddings,
    query_face_embeddings,
    search_index,
)
from face_search.utils import SUPPORTED_EXTENSIONS

LOGGER = logging.getLogger("face_search.api")
MAX_REQUEST_BYTES = 64 * 1024 * 1024


def _error_response(status: int, message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


def create_app(
    *,
    database: Path | None = None,
    photo_root: Path | None = None,
    cors_origins: list[str] | None = None,
    storage_backend: str | None = None,
) -> FastAPI:
    settings = Settings.load(
        database=database,
        photo_root=photo_root,
        cors_origins=cors_origins,
        storage_backend=storage_backend,
    )
    app = FastAPI(title="Find Your Pic API", docs_url=None, redoc_url=None)
    app.state.settings = settings
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type"],
    )

    def current_settings() -> Settings:
        return app.state.settings

    def storage():
        try:
            return service.storage_for(current_settings())
        except StorageError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.exception_handler(HTTPException)
    async def http_exception_handler(_request: Request, exc: HTTPException) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        return _error_response(exc.status_code, detail)

    @app.exception_handler(PermissionError)
    async def permission_handler(_request: Request, exc: PermissionError) -> JSONResponse:
        return _error_response(403, str(exc))

    @app.exception_handler(FileNotFoundError)
    async def missing_handler(_request: Request, exc: FileNotFoundError) -> JSONResponse:
        return _error_response(404, str(exc))

    @app.exception_handler(NoFaceError)
    async def no_face_handler(_request: Request, exc: NoFaceError) -> JSONResponse:
        return _error_response(400, str(exc))

    @app.exception_handler(ValueError)
    async def value_handler(_request: Request, exc: ValueError) -> JSONResponse:
        return _error_response(400, str(exc))

    @app.exception_handler(StorageError)
    async def storage_handler(_request: Request, exc: StorageError) -> JSONResponse:
        return _error_response(400, str(exc))

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/config")
    def config() -> dict[str, Any]:
        return service.config_payload(current_settings())

    @app.get("/api/collection-name")
    def get_collection_name() -> dict[str, str]:
        cfg = current_settings()
        return {"name": service.collection_name(cfg.database, cfg.collection_root())}

    @app.post("/api/collection-name")
    def post_collection_name(payload: dict[str, Any]) -> dict[str, str]:
        name = payload.get("name")
        if not isinstance(name, str):
            raise ValueError("Collection name cannot be empty.")
        return {"name": service.set_collection_name(current_settings().database, name)}

    @app.get("/api/feedback")
    def get_feedback() -> dict[str, Any]:
        return {"feedback": service.list_feedback(current_settings().database)}

    @app.get("/api/photos")
    def photos(
        page: int = 1,
        page_size: int = 24,
        q: str = "",
        kind: str = "all",
        sort: str = "newest",
    ) -> dict[str, Any]:
        if page < 1 or page_size < 1:
            raise ValueError("Page and page size must be integers.")
        return service.list_photos(
            current_settings().database,
            storage(),
            page=page,
            page_size=min(service.MAX_PAGE_SIZE, page_size),
            query=q.strip().lower(),
            kind=(kind or "all").strip().lower(),
            sort=(sort or "newest").strip().lower(),
        )

    @app.get("/api/people")
    def people(page: int = 1, page_size: int = 24, q: str = "") -> dict[str, Any]:
        if page < 1 or page_size < 1:
            raise ValueError("Page and page size must be integers.")
        return service.list_people(
            current_settings().database,
            page=page,
            page_size=min(service.MAX_PAGE_SIZE, page_size),
            query=q.strip().casefold(),
        )

    @app.get("/api/image")
    def image(path: str = "") -> Response:
        image_path = storage().resolve(path)
        media_type = mimetypes.guess_type(image_path.name)[0] or "application/octet-stream"
        return Response(
            content=image_path.read_bytes(),
            media_type=media_type,
            headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=300"},
        )

    @app.get("/api/face")
    def face(path: str = "", x1: str = "", y1: str = "", x2: str = "", y2: str = "") -> Response:
        image_path = storage().resolve(path)
        try:
            box = (int(x1), int(y1), int(x2), int(y2))
        except ValueError as exc:
            raise ValueError("Invalid face bounding box.") from exc
        body = service.crop_face_jpeg(image_path, box)
        return Response(
            content=body,
            media_type="image/jpeg",
            headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=300"},
        )

    @app.post("/api/index")
    def index_collection(payload: dict[str, Any]) -> dict[str, Any]:
        cfg = current_settings()
        collection = cfg.collection_root()
        backend = payload.get("backend", "insightface")
        if backend not in ("insightface", "dlib"):
            raise ValueError("Unsupported face backend.")
        workers = payload.get("workers", 1)
        if isinstance(workers, bool) or not isinstance(workers, int) or workers < 1:
            raise ValueError("Workers must be a positive integer.")
        report = index_directory(
            collection,
            cfg.database,
            backend,
            workers=workers,
            show_progress=False,
        )
        return {"report": service.report_payload(report), "stats": index_stats(cfg.database)}

    @app.post("/api/detect")
    def detect(payload: dict[str, Any]) -> dict[str, Any]:
        encoded = payload.get("data")
        filename = payload.get("name", "query.jpg")
        if not isinstance(encoded, str) or not isinstance(filename, str):
            raise ValueError("Upload a supported image file.")
        suffix = Path(filename).suffix.lower()
        if suffix not in SUPPORTED_EXTENSIONS:
            raise ValueError("Supported image types: JPG, PNG, WEBP, and BMP.")
        try:
            contents = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("The uploaded image data is invalid.") from exc
        if not contents or len(contents) > MAX_REQUEST_BYTES:
            raise ValueError("The uploaded image is empty or exceeds the 64 MB limit.")
        backend_name = payload.get("backend", "insightface")
        if backend_name not in ("insightface", "dlib"):
            raise ValueError("Unsupported face backend.")
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as image_file:
                image_file.write(contents)
                temporary_path = Path(image_file.name)
            faces = query_face_embeddings(temporary_path, create_backend(backend_name))
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
        token = service.remember_faces([embedding for _, embedding in faces])
        return {
            "token": token,
            "faces": [
                {
                    "index": index,
                    "bbox": {"x1": bbox.x1, "y1": bbox.y1, "x2": bbox.x2, "y2": bbox.y2},
                }
                for index, (bbox, _) in enumerate(faces)
            ],
        }

    @app.post("/api/search")
    def search(payload: dict[str, Any]) -> dict[str, Any]:
        queries = payload.get("queries")
        if not isinstance(queries, list) or not queries:
            raise ValueError("Select at least one detected query face.")
        selected_embeddings = []
        for selection in queries:
            if not isinstance(selection, dict):
                raise ValueError("Invalid face selection.")
            token = selection.get("token")
            face_index = selection.get("face_index")
            if not isinstance(token, str) or isinstance(face_index, bool) or not isinstance(face_index, int):
                raise ValueError("Invalid face selection.")
            faces = service.get_cached_faces(token)
            if face_index < 0 or face_index >= len(faces):
                raise ValueError("Selected face does not exist.")
            selected_embeddings.append(faces[face_index])
        threshold = payload.get("threshold", 0.45)
        top_n = payload.get("top_n", 50)
        if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
            raise ValueError("Similarity threshold must be numeric.")
        if isinstance(top_n, bool) or not isinstance(top_n, int):
            raise ValueError("Maximum results must be an integer.")
        database = current_settings().database
        matches = search_index(
            average_embeddings(selected_embeddings),
            database,
            float(threshold),
            top_n,
        )
        return {
            "matches": [
                {
                    "score": match.score,
                    "path": match.path,
                    "face_index": match.face_index,
                    "bbox": {
                        "x1": match.bbox.x1,
                        "y1": match.bbox.y1,
                        "x2": match.bbox.x2,
                        "y2": match.bbox.y2,
                    },
                }
                for match in matches
            ],
            "stats": index_stats(database) if database.exists() else {},
        }

    @app.post("/api/person-name")
    def person_name(payload: dict[str, Any]) -> dict[str, str]:
        key, name = payload.get("key"), payload.get("name")
        if not isinstance(key, str) or not isinstance(name, str):
            raise ValueError("Invalid person group.")
        saved = service.set_person_name(current_settings().database, key, name)
        return {"key": key, "name": saved}

    @app.post("/api/feedback")
    def set_feedback(payload: dict[str, Any]) -> dict[str, Any]:
        path, value = payload.get("path"), payload.get("value")
        if not isinstance(path, str) or value not in ("like", "up", "down", None):
            raise ValueError("Invalid photo feedback.")
        cfg = current_settings()
        store = storage()
        if value in ("like", "up"):
            _copy_to_folder(store, path, "liked")
        elif value == "down":
            _remove_feedback_copies(store, path)
            _copy_to_folder(store, path, "disliked")
        else:
            _remove_feedback_copies(store, path)
        if value in ("like", "up"):
            _remove_from_folder(store, path, "disliked")
        with sqlite3.connect(cfg.database) as connection:
            service.ensure_custom_tables(connection)
            if value is None:
                connection.execute("DELETE FROM photo_feedback WHERE path = ?", (path,))
            else:
                connection.execute(
                    "INSERT OR REPLACE INTO photo_feedback(path, value) VALUES (?, ?)",
                    (path, value),
                )
        return {"path": path, "value": value}

    @app.post("/api/delete-photo")
    def delete_photo(payload: dict[str, Any]) -> dict[str, Any]:
        path = payload.get("path")
        if not isinstance(path, str) or not path.strip():
            raise ValueError("Invalid photo path.")
        cfg = current_settings()
        store = storage()
        target = store.resolve(path)
        trash_dir = store.root / "deleted"
        trash_dir.mkdir(parents=True, exist_ok=True)
        safe_name = target.name
        counter = 1
        while (trash_dir / safe_name).exists():
            safe_name = f"{target.stem}_{counter}{target.suffix}"
            counter += 1
        moved = trash_dir / safe_name
        if target.exists() and target.is_file():
            shutil.copy2(target, moved)
            target.unlink()
        stored = str(target)
        with sqlite3.connect(cfg.database) as connection:
            service.ensure_custom_tables(connection)
            connection.execute("DELETE FROM files WHERE path = ?", (stored,))
            connection.execute("DELETE FROM faces WHERE path = ?", (stored,))
            connection.execute("DELETE FROM photo_feedback WHERE path = ?", (stored,))
            connection.commit()
        return {
            "deleted": True,
            "path": stored,
            "trash": store.relative_name(moved),
            "message": "Photo moved to trash.",
        }

    @app.post("/api/download-zip")
    def download_zip(payload: dict[str, Any]) -> StreamingResponse:
        requested_paths = payload.get("paths")
        if not isinstance(requested_paths, list) or not requested_paths:
            raise ValueError("Select at least one photo to download.")
        if any(not isinstance(path, str) or not path for path in requested_paths):
            raise ValueError("Invalid photo path in download request.")
        store = storage()
        unique_paths = list(dict.fromkeys(requested_paths))
        archive_file = tempfile.TemporaryFile()
        try:
            with zipfile.ZipFile(archive_file, "w", zipfile.ZIP_DEFLATED) as archive:
                for requested in unique_paths:
                    image_path = store.resolve(requested)
                    archive.write(image_path, store.relative_name(image_path))
            archive_file.seek(0)

            def iterator():
                try:
                    while True:
                        chunk = archive_file.read(1024 * 1024)
                        if not chunk:
                            break
                        yield chunk
                finally:
                    archive_file.close()

            return StreamingResponse(
                iterator(),
                media_type="application/zip",
                headers={
                    "Content-Disposition": 'attachment; filename="selected-photos.zip"',
                    "Cache-Control": "no-store",
                    "X-Content-Type-Options": "nosniff",
                },
            )
        except Exception:
            archive_file.close()
            raise

    @app.post("/api/clear")
    def clear() -> dict[str, Any]:
        database = current_settings().database
        clear_index(database)
        return {"cleared": True, "stats": index_stats(database)}

    _mount_frontend(app)
    return app


def _copy_to_folder(store: Any, path: str, folder_name: str) -> None:
    try:
        source_path = store.resolve(path)
    except (PermissionError, FileNotFoundError, StorageError):
        return
    target_dir = store.root / folder_name
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / source_path.name
    counter = 1
    while target_path.exists() and target_path.resolve() != source_path.resolve():
        target_path = target_dir / f"{source_path.stem}_{counter}{source_path.suffix}"
        counter += 1
    if target_path.resolve() != source_path.resolve():
        shutil.copy2(source_path, target_path)


def _remove_from_folder(store: Any, path: str, folder_name: str) -> None:
    candidate = store.root / folder_name / Path(path).name
    if candidate.exists():
        candidate.unlink(missing_ok=True)


def _remove_feedback_copies(store: Any, path: str) -> None:
    for folder_name in ("liked", "disliked"):
        _remove_from_folder(store, path, folder_name)


def _mount_frontend(app: FastAPI) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    react_dist = repo_root / "apps" / "frontend" / "dist"
    legacy = Path(__file__).resolve().parents[2] / "face_search" / "web"
    # When running from installed layout, also check next to the package.
    if not legacy.is_dir():
        legacy = Path(__file__).resolve().parents[2]

    if react_dist.is_dir():
        index_file = react_dist / "index.html"

        @app.get("/")
        def spa_index() -> Any:
            return _file_response(index_file, "text/html; charset=utf-8")

        app.mount("/", StaticFiles(directory=react_dist, html=True), name="web")
        return
    if legacy.is_dir() and (legacy / "index.html").is_file():
        @app.get("/")
        def legacy_index() -> Any:
            return _file_response(legacy / "index.html", "text/html; charset=utf-8")

        @app.get("/assets/app.css")
        def legacy_css() -> Any:
            return _file_response(legacy / "app.css", "text/css")

        @app.get("/assets/app.js")
        def legacy_js() -> Any:
            return _file_response(legacy / "app.js", "application/javascript")


def _file_response(path: Path, media_type: str) -> Response:
    return Response(content=path.read_bytes(), media_type=media_type)


app = create_app()
