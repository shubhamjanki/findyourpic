"""Index helpers shared by FastAPI routes."""

from __future__ import annotations

import sqlite3
import threading
import time
import uuid
from collections import OrderedDict
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageOps

from apps.backend.settings import Settings
from apps.backend.storage import LocalStorage, create_storage
from face_search.index import index_stats
from face_search.utils import SUPPORTED_EXTENSIONS

PERSON_MATCH_THRESHOLD = 0.48
MAX_PAGE_SIZE = 60
MAX_CACHED_QUERIES = 64
QUERY_TTL_SECONDS = 15 * 60

_query_lock = threading.Lock()
_query_cache: OrderedDict[str, tuple[float, list[Any]]] = OrderedDict()


def remember_faces(faces: list[Any]) -> str:
    now = time.monotonic()
    token = uuid.uuid4().hex
    with _query_lock:
        expired = [
            key for key, (created, _) in _query_cache.items()
            if now - created > QUERY_TTL_SECONDS
        ]
        for key in expired:
            del _query_cache[key]
        _query_cache[token] = (now, faces)
        while len(_query_cache) > MAX_CACHED_QUERIES:
            _query_cache.popitem(last=False)
    return token


def get_cached_faces(token: str) -> list[Any]:
    with _query_lock:
        cached = _query_cache.get(token)
        if cached is None or time.monotonic() - cached[0] > QUERY_TTL_SECONDS:
            _query_cache.pop(token, None)
            raise ValueError("This query photo has expired. Upload it again to search.")
        _query_cache.move_to_end(token)
        return cached[1]


def table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone() is not None


def ensure_custom_tables(connection: sqlite3.Connection) -> None:
    connection.execute(
        "CREATE TABLE IF NOT EXISTS person_labels (person_key TEXT PRIMARY KEY, name TEXT NOT NULL)"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS photo_feedback ("
        "path TEXT PRIMARY KEY, value TEXT NOT NULL CHECK(value IN ('like', 'up', 'down')))"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS ui_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )


def storage_for(settings: Settings) -> LocalStorage:
    return create_storage(settings.collection_root(), settings.storage_backend)


def crop_face_jpeg(image_path: Path, box: tuple[int, int, int, int]) -> bytes:
    from io import BytesIO

    with Image.open(image_path) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
        x1, y1, x2, y2 = box
        padding = max(12, (x2 - x1) // 3, (y2 - y1) // 3)
        crop = image.crop((
            max(0, x1 - padding),
            max(0, y1 - padding),
            min(image.width, x2 + padding),
            min(image.height, y2 + padding),
        ))
        crop.thumbnail((320, 320))
        output = BytesIO()
        crop.save(output, format="JPEG", quality=88)
        return output.getvalue()


def collection_name(database: Path, fallback_root: Path) -> str:
    fallback = fallback_root.name or "My collection"
    if not database.exists():
        return fallback
    with sqlite3.connect(database) as connection:
        if not table_exists(connection, "ui_settings"):
            return fallback
        row = connection.execute(
            "SELECT value FROM ui_settings WHERE key = 'collection_name'"
        ).fetchone()
    return row[0] if row else fallback


def set_collection_name(database: Path, name: str) -> str:
    cleaned = name.strip()[:80]
    if not cleaned:
        raise ValueError("Collection name cannot be empty.")
    database.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(database) as connection:
        ensure_custom_tables(connection)
        connection.execute(
            "INSERT OR REPLACE INTO ui_settings(key, value) VALUES ('collection_name', ?)",
            (cleaned,),
        )
    return cleaned


def set_person_name(database: Path, key: str, name: str) -> str:
    if not key:
        raise ValueError("Invalid person group.")
    cleaned = name.strip()[:80]
    if not cleaned:
        raise ValueError("Person name cannot be empty.")
    with sqlite3.connect(database) as connection:
        ensure_custom_tables(connection)
        connection.execute(
            "INSERT OR REPLACE INTO person_labels(person_key, name) VALUES (?, ?)",
            (key, cleaned),
        )
    return cleaned


def list_feedback(database: Path) -> dict[str, str]:
    if not database.exists():
        return {}
    with sqlite3.connect(database) as connection:
        if not table_exists(connection, "photo_feedback"):
            return {}
        return dict(connection.execute("SELECT path, value FROM photo_feedback"))


def list_photos(
    database: Path,
    storage: LocalStorage,
    *,
    page: int,
    page_size: int,
    query: str,
    kind: str,
    sort: str,
) -> dict[str, Any]:
    if kind in {"liked", "disliked", "deleted"}:
        folder = storage.root / kind
        if not folder.exists():
            return {"photos": [], "total": 0, "page": page, "page_size": page_size, "kind": kind}
        entries = [
            path for path in folder.iterdir()
            if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
        ]
        if sort == "name":
            entries.sort(key=lambda path: path.name.lower())
        else:
            entries.sort(key=lambda path: (path.stat().st_mtime_ns, str(path)), reverse=True)
        total = len(entries)
        paged = entries[(page - 1) * page_size: page * page_size]
        photos = [{"path": str(path.relative_to(storage.root)), "faces": 1} for path in paged]
        return {"photos": photos, "total": total, "page": page, "page_size": page_size, "kind": kind}
    if not database.exists():
        return {"photos": [], "total": 0, "page": page, "page_size": page_size, "kind": kind}
    with sqlite3.connect(database) as connection:
        if query:
            pattern = f"%{query}%"
            total = connection.execute(
                "SELECT COUNT(*) FROM files WHERE lower(path) LIKE ?", (pattern,)
            ).fetchone()[0]
            rows = connection.execute(
                """SELECT f.path, (SELECT COUNT(*) FROM faces x WHERE x.path = f.path)
                   FROM files f WHERE lower(f.path) LIKE ?
                   ORDER BY f.path LIMIT ? OFFSET ?""",
                (pattern, page_size, (page - 1) * page_size),
            ).fetchall()
        else:
            ordering = "mtime_ns DESC" if sort == "newest" else "path ASC"
            total = connection.execute("SELECT COUNT(*) FROM files").fetchone()[0]
            rows = connection.execute(
                f"""SELECT f.path, (SELECT COUNT(*) FROM faces x WHERE x.path = f.path)
                   FROM files f ORDER BY {ordering} LIMIT ? OFFSET ?""",
                (page_size, (page - 1) * page_size),
            ).fetchall()
    return {
        "photos": [{"path": path, "faces": face_count} for path, face_count in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "kind": kind,
    }


def list_people(database: Path, *, page: int, page_size: int, query: str) -> dict[str, Any]:
    empty = {
        "people": [],
        "total": 0,
        "page": page,
        "page_size": page_size,
        "threshold": PERSON_MATCH_THRESHOLD,
    }
    if not database.exists():
        return empty
    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            """SELECT path, face_index, x1, y1, x2, y2, dimension, embedding
               FROM faces ORDER BY path, face_index"""
        ).fetchall()
        labels = dict(connection.execute(
            "SELECT person_key, name FROM person_labels"
        ).fetchall()) if table_exists(connection, "person_labels") else {}
    groups: list[dict[str, Any]] = []
    dimension = rows[0][6] if rows else 0
    centroid_matrix = np.empty((len(rows), dimension), dtype=np.float32)
    sums = np.empty_like(centroid_matrix)
    for path, face_index, x1, y1, x2, y2, dimension, blob in rows:
        vector = np.frombuffer(blob, dtype=np.float32, count=dimension)
        if not groups:
            best_index, best_score = None, -1.0
        else:
            scores = centroid_matrix[:len(groups)] @ vector
            best_index = int(np.argmax(scores))
            best_score = float(scores[best_index])
        member = {
            "path": path,
            "face_index": face_index,
            "bbox": {"x1": x1, "y1": y1, "x2": x2, "y2": y2},
        }
        if best_index is None or best_score < PERSON_MATCH_THRESHOLD:
            key = f"{path}#{face_index}"
            group_index = len(groups)
            groups.append({"key": key, "members": [member]})
            sums[group_index] = vector
            centroid_matrix[group_index] = vector
        else:
            group = groups[best_index]
            group["members"].append(member)
            sums[best_index] += vector
            centroid = sums[best_index] / len(group["members"])
            centroid_matrix[best_index] = centroid / max(float(np.linalg.norm(centroid)), 1e-12)
    groups.sort(key=lambda group: (-len(group["members"]), group["key"]))
    people = []
    for index, group in enumerate(groups, start=1):
        first = group["members"][0]
        photos = list({member["path"]: member for member in group["members"]}.values())
        people.append({
            "key": group["key"],
            "name": labels.get(group["key"], f"P{index}"),
            "count": len(photos),
            "preview": first,
            "photos": photos,
        })
    if query:
        people = [person for person in people if query in person["name"].casefold()]
        people.sort(key=lambda person: person["name"].casefold() != query)
    start = (page - 1) * page_size
    return {
        "people": people[start:start + page_size],
        "total": len(people),
        "page": page,
        "page_size": page_size,
        "threshold": PERSON_MATCH_THRESHOLD,
    }


def report_payload(report: Any) -> dict[str, Any]:
    return {
        "scanned": report.scanned,
        "indexed": report.indexed,
        "skipped": report.skipped,
        "removed": report.removed,
        "face_count": report.face_count,
        "failed": report.failed,
        "backend_changed": report.backend_changed,
        "source_changed": report.source_changed,
        "errors": report.errors,
    }


def config_payload(settings: Settings) -> dict[str, Any]:
    collection = settings.collection_root()
    return {
        "database": str(settings.database),
        "collection": str(collection),
        "stats": index_stats(settings.database) if settings.database.exists() else {},
        "backends": ["insightface", "dlib"],
        "storage": settings.storage_backend,
    }
