"""Index helpers shared by FastAPI routes."""

from __future__ import annotations

import os
import sqlite3
import threading
import time
import uuid
from collections import OrderedDict
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageOps

try:
    from backend.settings import Settings
    from backend.storage import LocalStorage, create_storage
except ImportError:
    try:
        from apps.backend.settings import Settings
        from apps.backend.storage import LocalStorage, create_storage
    except ImportError:
        from settings import Settings
        from storage import LocalStorage, create_storage
from face_search.index import index_stats
from face_search.utils import SUPPORTED_EXTENSIONS

PERSON_MATCH_THRESHOLD = 0.48
MAX_PAGE_SIZE = 60
MAX_CACHED_QUERIES = 64
QUERY_TTL_SECONDS = 15 * 60

_query_lock = threading.Lock()
_query_cache: OrderedDict[str, tuple[float, list[Any]]] = OrderedDict()

# ---------------------------------------------------------------------------
# Face thumbnail cache
# ---------------------------------------------------------------------------
# Cropping a 320 px thumbnail from a 20-50 MB RAW photo takes ~1 s per call.
# Cache up to 512 results in memory keyed by (resolved_path, box).
_face_cache_lock = threading.Lock()
_face_cache: OrderedDict[tuple, bytes] = OrderedDict()
_FACE_CACHE_MAX = 512


def _cache_face(key: tuple, data: bytes) -> bytes:
    with _face_cache_lock:
        _face_cache[key] = data
        _face_cache.move_to_end(key)
        while len(_face_cache) > _FACE_CACHE_MAX:
            _face_cache.popitem(last=False)
    return data


# ---------------------------------------------------------------------------
# People-grouping result cache
# ---------------------------------------------------------------------------
# The cosine-similarity clustering over all embeddings is O(N·G) where N is
# the total face count and G is the number of groups — for 4500+ rows this
# can take > 1 s.  We cache the full result and invalidate it only when the
# database file's (mtime, size) pair changes (i.e. after re-indexing).
_people_cache_lock = threading.Lock()
_people_cache: dict[str, Any] = {}   # keys: 'key' (mtime,size), 'result'


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

    cache_key = (str(image_path), box)
    with _face_cache_lock:
        cached = _face_cache.get(cache_key)
        if cached is not None:
            _face_cache.move_to_end(cache_key)
            return cached

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
        data = output.getvalue()

    return _cache_face(cache_key, data)
def crop_face_from_bytes(image_bytes: bytes, box: tuple[int, int, int, int]) -> bytes:
    """Crop a face thumbnail from raw image bytes (used with S3 storage)."""
    from io import BytesIO

    cache_key = (hash(image_bytes), box)
    with _face_cache_lock:
        cached = _face_cache.get(cache_key)
        if cached is not None:
            _face_cache.move_to_end(cache_key)
            return cached

    with Image.open(BytesIO(image_bytes)) as source:
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
        data = output.getvalue()

    return _cache_face(cache_key, data)



def collection_name(database: Path, fallback_root: Path) -> str:
    fallback = fallback_root.name or "My collection"
    if fallback == "photos":
        fallback = "S3 Photos"
    if not database.exists():
        return fallback
    with sqlite3.connect(database) as connection:
        if not table_exists(connection, "ui_settings"):
            return fallback
        row = connection.execute(
            "SELECT value FROM ui_settings WHERE key = 'collection_name'"
        ).fetchone()
    return row[0] if row else fallback


def index_s3(
    store: Any,
    database: Path,
    backend_name: str = "insightface",
    max_dimension: int = 1600,
    progress_callback: Any = None,
) -> Any:
    from io import BytesIO
    from face_search.backends import create_backend
    from face_search.backends.base import BoundingBox
    from face_search.index import IndexReport, ProcessedImage, IndexedFace, _schema, _store_result
    from face_search.utils import normalize_embedding

    database = database.expanduser().resolve()
    database.parent.mkdir(parents=True, exist_ok=True)

    items = store.list_all_keys()
    report = IndexReport(scanned=len(items))
    if progress_callback is not None:
        progress_callback("checking", 0, len(items))

    backend = create_backend(backend_name)
    source_root = f"s3://{store.bucket}/{store.prefix}"

    with sqlite3.connect(database) as connection:
        _schema(connection)
        previous_backend = connection.execute(
            "SELECT value FROM metadata WHERE key = 'backend'"
        ).fetchone()
        if previous_backend and previous_backend[0] != backend_name:
            connection.execute("DELETE FROM files")
            report.backend_changed = True

        connection.execute(
            "INSERT OR REPLACE INTO metadata(key, value) VALUES ('backend', ?)",
            (backend_name,),
        )
        connection.execute(
            "INSERT OR REPLACE INTO metadata(key, value) VALUES ('source_root', ?)",
            (source_root,),
        )

        existing = {
            row[0]: (row[1], row[2], row[3])
            for row in connection.execute("SELECT path, mtime_ns, size, sha256 FROM files")
        }
        discovered_keys = {item["key"] for item in items}
        deleted = {path for path in existing if path not in discovered_keys}
        if deleted:
            connection.executemany("DELETE FROM files WHERE path = ?", [(p,) for p in deleted])
            report.removed = len(deleted)

        tasks = []
        for checked, item in enumerate(items, start=1):
            key = item["key"]
            old = existing.get(key)
            if old and old[1] == item["size"] and old[2] == item["etag"]:
                report.skipped += 1
            else:
                tasks.append(item)
            if progress_callback is not None:
                progress_callback("checking", checked, len(items))

        if progress_callback is not None:
            progress_callback("indexing", 0, len(tasks))

        for completed, item in enumerate(tasks, start=1):
            key = item["key"]
            processed = ProcessedImage(key, item["mtime_ns"], item["size"], item["etag"])
            try:
                data = store.read_image(key)
                with Image.open(BytesIO(data)) as source:
                    source = ImageOps.exif_transpose(source).convert("RGB")
                    original_size = source.size
                    if max(original_size) > max_dimension:
                        scale = max_dimension / max(original_size)
                        new_size = (max(1, round(original_size[0] * scale)), max(1, round(original_size[1] * scale)))
                        resized = source.resize(new_size, Image.Resampling.BILINEAR)
                        image = np.asarray(resized)
                        scale_x = image.shape[1] / original_size[0]
                        scale_y = image.shape[0] / original_size[1]
                    else:
                        image = np.asarray(source)
                        scale_x = 1.0
                        scale_y = 1.0

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

            _store_result(connection, processed, report)
            if progress_callback is not None:
                progress_callback("indexing", completed, len(tasks))

        connection.commit()
    return report


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
    storage: Any = None,
    *,
    page: int,
    page_size: int,
    query: str,
    kind: str,
    sort: str,
) -> dict[str, Any]:
    if not database.exists():
        return {"photos": [], "total": 0, "page": page, "page_size": page_size, "kind": kind}
    with sqlite3.connect(database) as connection:
        ensure_custom_tables(connection)
        if kind in {"liked", "disliked"}:
            target_value = "like" if kind == "liked" else "down"
            base_query = "FROM files f JOIN photo_feedback fb ON fb.path = f.path WHERE fb.value = ?"
            params: list[Any] = [target_value]
            if query:
                base_query += " AND lower(f.path) LIKE ?"
                params.append(f"%{query}%")
            total = connection.execute(f"SELECT COUNT(*) {base_query}", params).fetchone()[0]
            ordering = "f.mtime_ns DESC" if sort == "newest" else "f.path ASC"
            rows = connection.execute(
                f"""SELECT f.path, (SELECT COUNT(*) FROM faces x WHERE x.path = f.path)
                   {base_query} ORDER BY {ordering} LIMIT ? OFFSET ?""",
                params + [page_size, (page - 1) * page_size],
            ).fetchall()
            return {
                "photos": [{"path": path, "faces": face_count, "source": "s3"} for path, face_count in rows],
                "total": total,
                "page": page,
                "page_size": page_size,
                "kind": kind,
            }
        if kind == "deleted":
            return {"photos": [], "total": 0, "page": page, "page_size": page_size, "kind": kind}

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
        "photos": [{"path": path, "faces": face_count, "source": "s3"} for path, face_count in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "kind": kind,
    }


def _db_cache_key(database: Path) -> tuple[int, int] | None:
    """Return (mtime_ns, size) for the database file, or None if it does not exist."""
    try:
        st = database.stat()
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def _compute_people(database: Path) -> list[dict[str, Any]]:
    """Run the full cosine-similarity clustering and return the people list."""
    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            """SELECT path, face_index, x1, y1, x2, y2, dimension, embedding
               FROM faces ORDER BY path, face_index"""
        ).fetchall()
        labels = dict(connection.execute(
            "SELECT person_key, name FROM person_labels"
        ).fetchall()) if table_exists(connection, "person_labels") else {}
    if not rows:
        return []
    groups: list[dict[str, Any]] = []
    dimension = rows[0][6]
    centroid_matrix = np.empty((len(rows), dimension), dtype=np.float32)
    sums = np.empty_like(centroid_matrix)
    for path, face_index, x1, y1, x2, y2, dim, blob in rows:
        vector = np.frombuffer(blob, dtype=np.float32, count=dim)
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
    return people


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

    db_key = _db_cache_key(database)
    with _people_cache_lock:
        cached = _people_cache.get("result")
        if cached is not None and _people_cache.get("key") == db_key:
            all_people = cached
        else:
            all_people = _compute_people(database)
            _people_cache["key"] = db_key
            _people_cache["result"] = all_people

    # Apply per-request label overrides so renamed people reflect immediately
    # without busting the cache (labels are cheap to re-fetch).
    with sqlite3.connect(database) as connection:
        labels = dict(connection.execute(
            "SELECT person_key, name FROM person_labels"
        ).fetchall()) if table_exists(connection, "person_labels") else {}

    people = [
        {**p, "name": labels.get(p["key"], p["name"])}
        for p in all_people
    ]

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
    host_root = os.environ.get("HOST_PHOTO_ROOT", "").strip()
    stats = index_stats(settings.database) if settings.database.exists() else {}
    indexed_root = stats.get("root") if isinstance(stats, dict) else None
    if settings.storage_backend == "s3":
        collection = "S3 Photos"
    elif host_root:
        collection = host_root
    elif indexed_root and not indexed_root.startswith("/"):
        collection = indexed_root
    else:
        root_str = str(settings.collection_root())
        collection = "images" if root_str in ("/photos", "photos", "/data/photos") else root_str
    return {
        "database": str(settings.database),
        "collection": collection,
        "stats": stats,
        "backends": ["insightface", "dlib"],
        "storage": settings.storage_backend,
    }
