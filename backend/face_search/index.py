"""Incremental SQLite image index."""

from __future__ import annotations

import logging
import sqlite3
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np
from tqdm import tqdm

from face_search.backends import FaceBackend, create_backend
from face_search.backends.base import BoundingBox
from face_search.utils import DEFAULT_DATABASE, iter_image_paths, load_rgb_image, normalize_embedding, sha256_file

LOGGER = logging.getLogger(__name__)
_WORKER_BACKEND: FaceBackend | None = None


@dataclass
class IndexedFace:
    face_index: int
    bbox: BoundingBox
    embedding: np.ndarray


@dataclass
class ProcessedImage:
    relative_path: str
    mtime_ns: int
    size: int
    sha256: str
    faces: list[IndexedFace] = field(default_factory=list)
    error: str | None = None


@dataclass
class IndexReport:
    scanned: int = 0
    indexed: int = 0
    skipped: int = 0
    removed: int = 0
    face_count: int = 0
    failed: int = 0
    backend_changed: bool = False
    source_changed: bool = False
    errors: list[str] = field(default_factory=list)


def _schema(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS files (
            path TEXT PRIMARY KEY,
            mtime_ns INTEGER NOT NULL,
            size INTEGER NOT NULL,
            sha256 TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS faces (
            id INTEGER PRIMARY KEY,
            path TEXT NOT NULL REFERENCES files(path) ON DELETE CASCADE,
            face_index INTEGER NOT NULL,
            x1 INTEGER NOT NULL,
            y1 INTEGER NOT NULL,
            x2 INTEGER NOT NULL,
            y2 INTEGER NOT NULL,
            dimension INTEGER NOT NULL,
            embedding BLOB NOT NULL,
            UNIQUE(path, face_index)
        );
        CREATE INDEX IF NOT EXISTS faces_path_idx ON faces(path);
        """
    )


def _process_image(
    path: Path, relative_path: str, mtime_ns: int, size: int, digest: str,
    max_dimension: int, backend: FaceBackend,
) -> ProcessedImage:
    processed = ProcessedImage(relative_path, mtime_ns, size, digest)
    try:
        image, scale_x, scale_y, original_size = load_rgb_image(path, max_dimension)
        for face_index, detection in enumerate(backend.detect(image)):
            raw_box = detection.bbox
            x1 = min(original_size[0], max(0, round(raw_box.x1 / scale_x)))
            y1 = min(original_size[1], max(0, round(raw_box.y1 / scale_y)))
            x2 = min(original_size[0], max(x1, round(raw_box.x2 / scale_x)))
            y2 = min(original_size[1], max(y1, round(raw_box.y2 / scale_y)))
            embedding = normalize_embedding(backend.embed(image, detection))
            processed.faces.append(
                IndexedFace(face_index, BoundingBox(x1, y1, x2, y2), embedding)
            )
    except Exception as exc:
        processed.error = f"{type(exc).__name__}: {exc}"
    return processed


def _initialize_worker(backend_name: str) -> None:
    global _WORKER_BACKEND
    _WORKER_BACKEND = create_backend(backend_name)


def _process_image_worker(
    task: tuple[Path, str, int, int, str, int]
) -> ProcessedImage:
    if _WORKER_BACKEND is None:
        raise RuntimeError("Index worker backend was not initialized.")
    return _process_image(*task, _WORKER_BACKEND)


def _stored_path(path: Path) -> str:
    return str(path.resolve())


def _is_indexed_under_root(stored_path: str, root: Path) -> bool:
    try:
        Path(stored_path).resolve().relative_to(root)
        return True
    except (ValueError, OSError):
        return False


def index_directory(
    image_root: Path,
    database: Path = DEFAULT_DATABASE,
    backend_name: str = "insightface",
    backend: FaceBackend | None = None,
    workers: int = 1,
    max_dimension: int = 1600,
    show_progress: bool = True,
    progress_callback: Callable[[str, int, int], None] | None = None,
) -> IndexReport:
    root = image_root.expanduser().resolve()
    paths = iter_image_paths(root)
    if progress_callback is not None:
        progress_callback("checking", 0, len(paths))
    if workers < 1:
        raise ValueError("workers must be at least 1")
    if max_dimension < 1:
        raise ValueError("max_dimension must be positive")
    if backend is not None and workers > 1:
        raise ValueError("Custom backend instances support workers=1 only.")
    database = database.expanduser().resolve()
    database.parent.mkdir(parents=True, exist_ok=True)
    report = IndexReport(scanned=len(paths))
    owned_backend = backend is None and workers == 1
    active_backend = backend or (create_backend(backend_name) if owned_backend else None)

    with sqlite3.connect(database) as connection:
        _schema(connection)
        previous_backend = connection.execute(
            "SELECT value FROM metadata WHERE key = 'backend'"
        ).fetchone()
        previous_source = connection.execute(
            "SELECT value FROM metadata WHERE key = 'source_root'"
        ).fetchone()
        if previous_backend and previous_backend[0] != backend_name:
            connection.execute("DELETE FROM files")
            report.backend_changed = True
            LOGGER.warning(
                "Backend changed from %s to %s; rebuilding the index.",
                previous_backend[0], backend_name,
            )
        if previous_source and Path(previous_source[0]).expanduser().resolve() != root:
            report.source_changed = True
            LOGGER.warning(
                "Source root changed from %s to %s; updating the index metadata.",
                previous_source[0], str(root),
            )
        connection.execute(
            "INSERT OR REPLACE INTO metadata(key, value) VALUES ('backend', ?)",
            (backend_name,),
        )
        connection.execute(
            "INSERT OR REPLACE INTO metadata(key, value) VALUES ('source_root', ?)",
            (str(root),),
        )
        connection.execute(
            "INSERT OR IGNORE INTO metadata(key, value) VALUES (?, ?)",
            (f"source_root:{str(root)}", str(root)),
        )
        existing = {
            row[0]: (row[1], row[2], row[3])
            for row in connection.execute("SELECT path, mtime_ns, size, sha256 FROM files")
        }
        discovered = {_stored_path(path) for path in paths}
        deleted = {
            path for path in existing
            if path not in discovered and _is_indexed_under_root(path, root)
        }
        if deleted:
            connection.executemany("DELETE FROM files WHERE path = ?", [(path,) for path in deleted])
            report.removed = len(deleted)

        tasks: list[tuple[Path, str, int, int, str, int]] = []
        for checked, path in enumerate(paths, start=1):
            relative_path = _stored_path(path)
            try:
                stat = path.stat()
                digest = sha256_file(path)
            except OSError as exc:
                report.failed += 1
                report.errors.append(f"{relative_path}: {exc}")
                if progress_callback is not None:
                    progress_callback("checking", checked, len(paths))
                continue
            old = existing.get(relative_path)
            if old == (stat.st_mtime_ns, stat.st_size, digest):
                report.skipped += 1
                if progress_callback is not None:
                    progress_callback("checking", checked, len(paths))
                continue
            tasks.append((path, relative_path, stat.st_mtime_ns, stat.st_size, digest, max_dimension))
            if progress_callback is not None:
                progress_callback("checking", checked, len(paths))

        if progress_callback is not None:
            progress_callback("indexing", 0, len(tasks))
        progress = tqdm(total=len(tasks), desc="Indexing", unit="image", disable=not show_progress)
        try:
            if workers > 1 and tasks:
                with ProcessPoolExecutor(
                    max_workers=workers,
                    initializer=_initialize_worker,
                    initargs=(backend_name,),
                ) as executor:
                    results = executor.map(_process_image_worker, tasks, chunksize=1)
                    for completed, result in enumerate(results, start=1):
                        _store_result(connection, result, report)
                        progress.update(1)
                        if progress_callback is not None:
                            progress_callback("indexing", completed, len(tasks))
            else:
                selected_backend = active_backend or create_backend(backend_name)
                for completed, task in enumerate(tasks, start=1):
                    result = _process_image(*task, selected_backend)
                    _store_result(connection, result, report)
                    progress.update(1)
                    if progress_callback is not None:
                        progress_callback("indexing", completed, len(tasks))
        finally:
            progress.close()
        connection.commit()
    return report


def _store_result(
    connection: sqlite3.Connection, result: ProcessedImage, report: IndexReport
) -> None:
    if result.error:
        connection.execute("DELETE FROM files WHERE path = ?", (result.relative_path,))
        report.failed += 1
        report.errors.append(f"{result.relative_path}: {result.error}")
        LOGGER.error("Could not index %s: %s", result.relative_path, result.error)
        return
    connection.execute("DELETE FROM files WHERE path = ?", (result.relative_path,))
    connection.execute(
        "INSERT INTO files(path, mtime_ns, size, sha256) VALUES (?, ?, ?, ?)",
        (result.relative_path, result.mtime_ns, result.size, result.sha256),
    )
    connection.executemany(
        """INSERT INTO faces(path, face_index, x1, y1, x2, y2, dimension, embedding)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            (
                result.relative_path, face.face_index, face.bbox.x1, face.bbox.y1,
                face.bbox.x2, face.bbox.y2, face.embedding.size,
                face.embedding.astype(np.float32).tobytes(),
            )
            for face in result.faces
        ],
    )
    report.indexed += 1
    report.face_count += len(result.faces)


def index_stats(database: Path = DEFAULT_DATABASE) -> dict[str, int | str | None]:
    database = database.expanduser()
    if not database.exists():
        return {"backend": None, "root": None, "files": 0, "faces": 0}
    with sqlite3.connect(database) as connection:
        _schema(connection)
        backend = connection.execute(
            "SELECT value FROM metadata WHERE key = 'backend'"
        ).fetchone()
        root = connection.execute(
            "SELECT value FROM metadata WHERE key = 'source_root'"
        ).fetchone()
        files = connection.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        faces = connection.execute("SELECT COUNT(*) FROM faces").fetchone()[0]
    return {
        "backend": backend[0] if backend else None,
        "root": root[0] if root else None,
        "files": files,
        "faces": faces,
    }


def clear_index(database: Path = DEFAULT_DATABASE) -> None:
    database = database.expanduser()
    if not database.exists():
        return
    with sqlite3.connect(database) as connection:
        _schema(connection)
        connection.execute("DELETE FROM faces")
        connection.execute("DELETE FROM files")
        connection.execute("DELETE FROM metadata")
