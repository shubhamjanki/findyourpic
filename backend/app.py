"""FastAPI process for Find Your Pic. Bind through Docker to localhost on the host."""

from __future__ import annotations

import base64
import binascii
from io import BytesIO
import logging
import mimetypes
import os
import shutil
import sqlite3
import tempfile
import threading
import zipfile
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from PIL import Image, UnidentifiedImageError

try:
    from backend.settings import Settings
    from backend.storage import StorageError, create_s3_storage
    from backend import service
except ImportError:
    try:
        from apps.backend.settings import Settings
        from apps.backend.storage import StorageError, create_s3_storage
        from apps.backend import service
    except ImportError:
        from settings import Settings
        from storage import StorageError, create_s3_storage
        import service
from face_search.backends import create_backend
from face_search.index import clear_index, index_directory, index_stats
from face_search.search import (
    NoFaceError,
    average_embeddings,
    query_face_embeddings,
    search_index,
)
from face_search.utils import SUPPORTED_EXTENSIONS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
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
    app.state.index_run_lock = threading.Lock()
    app.state.index_job_lock = threading.Lock()
    app.state.index_job = None
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type"],
    )

    def current_settings() -> Settings:
        return app.state.settings


    def s3_storage():
        try:
            return create_s3_storage()
        except StorageError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    def require_s3_browsing() -> None:
        if os.environ.get("S3_BROWSE_ENABLED", "").strip().lower() != "true":
            raise HTTPException(status_code=404, detail="S3 image browsing is disabled.")

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

    @app.get("/")
    @app.head("/")
    def root() -> dict[str, str]:
        return {
            "name": "Find Your Pic API",
            "status": "ok",
            "frontend": "https://findyourpic.vercel.app",
        }

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/api/storage/s3/check-upload")
    def s3_check_upload(payload: dict[str, Any]) -> dict[str, Any]:
        if os.environ.get("S3_CHECK_UPLOAD_ENABLED", "").strip().lower() != "true":
            raise HTTPException(status_code=404, detail="S3 smoke uploads are disabled.")
        filename = payload.get("name")
        encoded = payload.get("data")
        if not isinstance(filename, str) or not isinstance(encoded, str):
            raise ValueError("Provide an image filename and base64-encoded image data.")
        suffix = Path(filename).suffix.lower()
        if suffix not in SUPPORTED_EXTENSIONS:
            raise ValueError("Supported image types: JPG, PNG, WEBP, and BMP.")
        try:
            contents = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("The uploaded image data is invalid.") from exc
        if not contents or len(contents) > MAX_REQUEST_BYTES:
            raise ValueError("The uploaded image is empty or exceeds the 64 MB limit.")
        try:
            with Image.open(BytesIO(contents)) as image:
                image.verify()
        except (UnidentifiedImageError, OSError) as exc:
            raise ValueError("The uploaded data is not a valid image.") from exc

        content_type = mimetypes.guess_type(f"upload{suffix}")[0] or "application/octet-stream"
        return s3_storage().upload_check(contents, filename, content_type)

    @app.get("/api/s3/photos")
    def s3_photos(page_size: int = 24, start_after: str | None = None) -> dict[str, Any]:
        require_s3_browsing()
        result = s3_storage().list_images(page_size=page_size, start_after=start_after)
        return result

    @app.get("/api/s3/image")
    def s3_image(key: str = "") -> Response:
        require_s3_browsing()
        contents = s3_storage().read_image(key)
        media_type = mimetypes.guess_type(key)[0] or "application/octet-stream"
        return Response(
            content=contents,
            media_type=media_type,
            headers={
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "private, max-age=300",
            },
        )

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
            s3_storage(),
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
        contents = s3_storage().read_image(path)
        media_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
        return Response(
            content=contents,
            media_type=media_type,
            headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=300"},
        )

    @app.get("/api/face")
    def face(path: str = "", x1: str = "", y1: str = "", x2: str = "", y2: str = "") -> Response:
        image_bytes = s3_storage().read_image(path)
        try:
            box = (int(x1), int(y1), int(x2), int(y2))
        except ValueError as exc:
            raise ValueError("Invalid face bounding box.") from exc
        body = service.crop_face_from_bytes(image_bytes, box)
        return Response(
            content=body,
            media_type="image/jpeg",
            headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=300"},
        )

    def index_options(payload: dict[str, Any]) -> tuple[str, int]:
        backend = payload.get("backend", "insightface")
        if backend not in ("insightface", "dlib"):
            raise ValueError("Unsupported face backend.")
        workers = payload.get("workers", 1)
        if isinstance(workers, bool) or not isinstance(workers, int) or workers < 1:
            raise ValueError("Workers must be a positive integer.")
        return backend, workers

    @app.post("/api/index")
    def index_collection(payload: dict[str, Any]) -> dict[str, Any]:
        backend, workers = index_options(payload)
        if not app.state.index_run_lock.acquire(blocking=False):
            raise HTTPException(status_code=409, detail="Indexing is already in progress.")
        try:
            cfg = current_settings()
            if cfg.storage_backend == "s3":
                report = service.index_s3(
                    s3_storage(),
                    cfg.database,
                    backend,
                )
            else:
                report = index_directory(
                    cfg.collection_root(),
                    cfg.database,
                    backend,
                    workers=workers,
                    show_progress=False,
                )
            return {"report": service.report_payload(report), "stats": index_stats(cfg.database)}
        finally:
            app.state.index_run_lock.release()

    @app.post("/api/index/start", status_code=202)
    def start_index(payload: dict[str, Any]) -> dict[str, str]:
        backend, workers = index_options(payload)
        if not app.state.index_run_lock.acquire(blocking=False):
            raise HTTPException(status_code=409, detail="Indexing is already in progress.")

        job_id = uuid4().hex
        with app.state.index_job_lock:
            app.state.index_job = {
                "job_id": job_id,
                "status": "running",
                "phase": "checking",
                "completed": 0,
                "total": 0,
            }

        def update_job(**updates: Any) -> None:
            with app.state.index_job_lock:
                job = app.state.index_job
                if job is not None and job["job_id"] == job_id:
                    app.state.index_job = {**job, **updates}

        def run_index() -> None:
            cfg = current_settings()
            try:
                if cfg.storage_backend == "s3":
                    report = service.index_s3(
                        s3_storage(),
                        cfg.database,
                        backend,
                        progress_callback=lambda phase, completed, total: update_job(
                            phase=phase, completed=completed, total=total
                        ),
                    )
                else:
                    report = index_directory(
                        cfg.collection_root(),
                        cfg.database,
                        backend,
                        workers=workers,
                        show_progress=False,
                        progress_callback=lambda phase, completed, total: update_job(
                            phase=phase, completed=completed, total=total
                        ),
                    )
                update_job(
                    status="completed",
                    report=service.report_payload(report),
                    stats=index_stats(cfg.database),
                )
            except BaseException as exc:
                LOGGER.exception("Indexing job %s failed.", job_id)
                update_job(status="failed", error=str(exc))
            finally:
                app.state.index_run_lock.release()

        try:
            threading.Thread(target=run_index, name=f"index-{job_id}", daemon=True).start()
        except Exception:
            app.state.index_run_lock.release()
            raise
        return {"job_id": job_id}

    @app.get("/api/index/status/{job_id}")
    def index_status(job_id: str) -> dict[str, Any]:
        with app.state.index_job_lock:
            job = app.state.index_job
            if job is None or job["job_id"] != job_id:
                raise HTTPException(status_code=404, detail="Indexing job not found.")
            return dict(job)

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
        with sqlite3.connect(cfg.database) as connection:
            service.ensure_custom_tables(connection)
            connection.execute("DELETE FROM files WHERE path = ?", (path,))
            connection.execute("DELETE FROM faces WHERE path = ?", (path,))
            connection.execute("DELETE FROM photo_feedback WHERE path = ?", (path,))
            connection.commit()
        return {
            "deleted": True,
            "path": path,
            "trash": "",
            "message": "Photo removed from index.",
        }

    @app.post("/api/download-zip")
    def download_zip(payload: dict[str, Any]) -> StreamingResponse:
        requested_paths = payload.get("paths")
        if not isinstance(requested_paths, list) or not requested_paths:
            raise ValueError("Select at least one photo to download.")
        if any(not isinstance(path, str) or not path for path in requested_paths):
            raise ValueError("Invalid photo path in download request.")
        unique_paths = list(dict.fromkeys(requested_paths))
        archive_file = tempfile.TemporaryFile()
        try:
            with zipfile.ZipFile(archive_file, "w", zipfile.ZIP_DEFLATED) as archive:
                for requested in unique_paths:
                    image_bytes = s3_storage().read_image(requested)
                    safe_filename = Path(requested).name
                    archive.writestr(safe_filename, image_bytes)
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


app = create_app()
