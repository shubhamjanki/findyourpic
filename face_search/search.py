"""Query embedding preparation and cosine-similarity search."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import numpy as np

from face_search.backends.base import BoundingBox, FaceBackend
from face_search.utils import DEFAULT_DATABASE, load_rgb_image, normalize_embedding


class NoFaceError(ValueError):
    pass


@dataclass(frozen=True)
class SearchMatch:
    score: float
    path: str
    face_index: int
    bbox: BoundingBox


def query_face_embeddings(
    path: Path, backend: FaceBackend, max_dimension: int = 1600
) -> list[tuple[BoundingBox, np.ndarray]]:
    image, _, _, _ = load_rgb_image(path, max_dimension)
    detections = backend.detect(image)
    if not detections:
        raise NoFaceError(f"No face was detected in query image: {path}")
    return [
        (detection.bbox, normalize_embedding(backend.embed(image, detection)))
        for detection in detections
    ]


def average_embeddings(embeddings: Sequence[np.ndarray]) -> np.ndarray:
    if not embeddings:
        raise ValueError("At least one query embedding is required.")
    dimensions = {np.asarray(embedding).size for embedding in embeddings}
    if len(dimensions) != 1:
        raise ValueError("Query embeddings have incompatible dimensions.")
    return normalize_embedding(np.mean(np.stack(embeddings), axis=0))


def search_index(
    query_embedding: np.ndarray,
    database: Path = DEFAULT_DATABASE,
    threshold: float = 0.45,
    top_n: int = 50,
) -> list[SearchMatch]:
    if not -1.0 <= threshold <= 1.0:
        raise ValueError("threshold must be between -1 and 1")
    if top_n < 1:
        raise ValueError("top_n must be positive")
    query = normalize_embedding(query_embedding)
    database = database.expanduser()
    if not database.exists():
        raise FileNotFoundError(f"Face index does not exist: {database}")

    best_by_image: dict[str, SearchMatch] = {}
    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            "SELECT path, face_index, x1, y1, x2, y2, dimension, embedding FROM faces"
        )
        for path, face_index, x1, y1, x2, y2, dimension, blob in rows:
            if dimension != query.size:
                raise ValueError(
                    "Query embedding dimension does not match the index. "
                    "Use the same face backend used to build the index."
                )
            vector = np.frombuffer(blob, dtype=np.float32, count=dimension)
            score = float(np.dot(query, vector))
            if score < threshold:
                continue
            match = SearchMatch(
                score, path, face_index, BoundingBox(x1, y1, x2, y2)
            )
            previous = best_by_image.get(path)
            if previous is None or match.score > previous.score:
                best_by_image[path] = match
    return sorted(
        best_by_image.values(), key=lambda match: match.score, reverse=True
    )[:top_n]