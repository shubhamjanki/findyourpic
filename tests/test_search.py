from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image
import pytest

from face_search.index import index_directory
from face_search.search import (
    NoFaceError,
    average_embeddings,
    query_face_embeddings,
    search_index,
)
from test_support import FakeBackend, MultiFaceBackend


def _write_image(path: Path, color: tuple[int, int, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (20, 12), color).save(path)


def test_search_ranks_by_cosine_and_returns_face_box(tmp_path: Path) -> None:
    collection = tmp_path / "photos"
    _write_image(collection / "red.png", (255, 0, 0))
    _write_image(collection / "green.png", (0, 255, 0))
    database = tmp_path / "index.sqlite3"
    index_directory(collection, database, backend=FakeBackend(), show_progress=False)

    matches = search_index(np.array([1, 0, 0], dtype=np.float32), database, threshold=0.7, top_n=10)

    assert [match.path for match in matches] == [str((collection / "red.png").resolve())]
    assert matches[0].score == pytest.approx(1.0)
    assert matches[0].bbox.x2 > matches[0].bbox.x1


def test_query_with_no_face_fails_clearly(tmp_path: Path) -> None:
    path = tmp_path / "empty.png"
    _write_image(path, (0, 0, 0))

    with pytest.raises(NoFaceError, match="No face"):
        query_face_embeddings(path, FakeBackend())


def test_multiple_query_faces_are_exposed_for_selection(tmp_path: Path) -> None:
    path = tmp_path / "group.png"
    _write_image(path, (120, 80, 40))

    faces = query_face_embeddings(path, MultiFaceBackend())

    assert len(faces) == 2
    assert faces[0][0] != faces[1][0]


def test_query_embeddings_are_averaged_and_normalized() -> None:
    result = average_embeddings([np.array([1, 0]), np.array([1, 0])])
    assert np.linalg.norm(result) == pytest.approx(1.0)
    assert result.tolist() == pytest.approx([1.0, 0.0])